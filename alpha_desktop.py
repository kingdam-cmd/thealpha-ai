"""
Alpha — the desktop assistant for TheAlpha.

Runs on YOUR laptop. Takes a plain-English instruction, works out what it
means, then carries it out on this machine.

It holds no API key. It signs you in with your TheAlpha AI account and
sends requests through the Alpha proxy, which checks your account has
premium access.

It remembers the conversation, including across restarts — history is
kept in alpha_memory.json next to this file.

Run it with:  python alpha_desktop.py

While it's running:
  type an instruction   - carry it out
  type 'voice'          - switch to speaking your commands
  type 'text'           - switch back to typing
  type 'forget'         - clear the conversation memory
  type 'quit'           - stop
"""

import os
import json
import time
import queue
import getpass
import threading
import subprocess
import webbrowser
import urllib.parse

import requests
import pyautogui
import pyttsx3
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

# These two are safe to ship — the anon key is public by design.
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")

# Your deployed proxy. Change this to your real proxy address.
PROXY_URL = os.getenv("PROXY_URL", "https://alpha-proxy.onrender.com")

# Where the conversation is kept between runs.
MEMORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "alpha_memory.json")
# How many past messages to carry into each request. Higher means better
# recall but a bigger, slower, more expensive call each turn.
MEMORY_TURNS = 24

# Voice input is optional — Alpha still works by typing if these fail to import.
try:
    import speech_recognition as sr
    import sounddevice as sd
    import numpy as np
    VOICE_INPUT_AVAILABLE = True
except ImportError:
    VOICE_INPUT_AVAILABLE = False

SAMPLE_RATE = 16000
BLOCK_SECONDS = 0.1          # how often we check the volume
SILENCE_TO_STOP = 0.9        # quiet for this long after speech = you're done
MAX_WAIT_TO_START = 6.0      # give up if nobody speaks
MAX_RECORD_SECONDS = 15.0    # hard ceiling on one utterance

# Haiku is markedly faster than Sonnet and plenty for interpreting commands.
# Swap to "claude-sonnet-4-5" if you'd rather have deeper conversation.
MODEL = os.getenv("ALPHA_MODEL", "claude-haiku-4-5-20251001")

# Apps Alpha is allowed to open, and the command that launches each.
# Add your own here — the left side is what you say, the right is what Windows runs.
KNOWN_APPS = {
    "notepad": "notepad",
    "calculator": "calc",
    "paint": "mspaint",
    "explorer": "explorer",
    "file explorer": "explorer",
    "chrome": "chrome",
    "browser": "chrome",
    "word": "winword",
    "excel": "excel",
    "powerpoint": "powerpnt",
    "cmd": "cmd",
    "terminal": "cmd",
    "settings": "ms-settings:",
    "task manager": "taskmgr",
    "vs code": "code",
    "vscode": "code",
    "spotify": "spotify",
}

# Actions that change or end your session — Alpha always asks first.
NEEDS_CONFIRMATION = {"shutdown", "restart", "sleep", "lock"}

INSTRUCTION = """You are Alpha, a desktop assistant running on a Windows
laptop. You do two things: you hold a real conversation, and you carry out
actions on this computer.

## Who you are
- Your name is Alpha. You are the desktop companion to TheAlpha AI, the
  web assistant at thealpha-ai.onrender.com.
- Unlike the web version, you run locally on this person's own machine,
  which is why you can open programs, type, and control the system.
- You are a premium feature. Only accounts with premium access can use you.
- You are an AI. Say so plainly if asked. Don't pretend to be a person.

## Who made you
- Abolade Oluwadamola Farombi, known as KingDam. He founded The Alpha DAO
  and The Alpha Institute, and built you, TheAlpha AI, and the Institute's
  learning platform.
- If asked about him, keep it brief and factual. You don't know his
  personal details — where he lives, his age, his background, his contact
  details. Don't speculate or fill gaps.

## The Alpha DAO and The Alpha Institute
- The Alpha Institute is the learning platform run by The Alpha DAO, at
  https://alpha-dao-alpha.vercel.app
- It hosts course modules, assignments, quizzes, group competitions, live
  classes, and certificates on completion.
- It covers a range of courses, not only data analysis. Excel and data
  analysis is one course that has run.
- Sign-up is open. Each course has its own module code, and people enter
  the code for the course they want.
- Some courses are free. Paid courses are $30, though prices can change —
  suggest confirming on the site.
- You do NOT know the current course list, module codes, cohort dates, or
  which specific courses are free. If asked, say so and point to the site.
  Never invent a module code or a start date — acting on a wrong one costs
  someone money or a missed deadline.

## How you reply
Reply with ONE JSON object and nothing else. No markdown fences, no
explanation outside the JSON.

The JSON must have an "action" key. Valid actions and their extra keys:

{"action": "open_app", "app": "<name>"}          - launch a program
{"action": "open_website", "url": "<full url>"}  - open a site in the browser
{"action": "search_web", "query": "<terms>"}     - Google something
{"action": "type_text", "text": "<text>"}        - type text where the cursor is
{"action": "press_keys", "keys": ["ctrl","s"]}   - press a keyboard shortcut
{"action": "screenshot"}                          - save a screenshot
{"action": "volume", "direction": "up|down|mute"} - change system volume
{"action": "sleep"}                               - put the laptop to sleep
{"action": "lock"}                                - lock the screen
{"action": "shutdown"}                            - shut down
{"action": "restart"}                             - restart
{"action": "say"}                                 - just answer conversationally

Every reply must include a "speech" key — what you say out loud.

## Conversation
- You are given the recent conversation. Use it. If someone says "open it
  again" or "what did I just ask you", work it out from the history rather
  than asking them to repeat themselves.
- Because your reply is spoken aloud, keep it to a few sentences unless
  they've asked for detail. Be direct. No filler openers.
- If it's a question or chat rather than a command, use "say".
- If you're unsure, say so. Never invent facts, figures, or names.

Example:
User: open notepad
{"action": "open_app", "app": "notepad", "speech": "Opening Notepad."}

User: what is the Alpha Institute?
{"action": "say", "speech": "It's the learning platform run by The Alpha DAO. Courses with live classes, assignments and certificates. Some are free, paid ones are thirty dollars."}
"""


class Alpha:
    def __init__(self):
        if not SUPABASE_URL or not SUPABASE_ANON_KEY:
            raise SystemExit(
                "Missing SUPABASE_URL or SUPABASE_ANON_KEY in your .env file."
            )
        self.supabase = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
        self.token = None
        self.voice_input = False
        self.recognizer = sr.Recognizer() if VOICE_INPUT_AVAILABLE else None
        self.history = self.load_memory()

    # ---------- memory ----------

    def load_memory(self):
        """Bring back the conversation from the last session."""
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data[-MEMORY_TURNS:]
        except Exception:
            pass
        return []

    def save_memory(self):
        try:
            with open(MEMORY_FILE, "w", encoding="utf-8") as f:
                json.dump(self.history[-MEMORY_TURNS:], f, ensure_ascii=False)
        except Exception:
            pass

    def forget(self):
        self.history = []
        try:
            if os.path.exists(MEMORY_FILE):
                os.remove(MEMORY_FILE)
        except Exception:
            pass

    def remember(self, role, content):
        self.history.append({"role": role, "content": content})
        self.history = self.history[-MEMORY_TURNS:]
        self.save_memory()

    # ---------- signing in ----------

    def sign_in(self):
        """Ask for the same credentials used on TheAlpha AI website."""
        print("Sign in with your TheAlpha AI account.\n")
        for _ in range(3):
            email = input("Email: ").strip()
            password = getpass.getpass("Password: ")
            try:
                result = self.supabase.auth.sign_in_with_password(
                    {"email": email, "password": password}
                )
                if result.session:
                    self.token = result.session.access_token
                    print("\nSigned in.\n")
                    return True
            except Exception as e:
                print(f"Couldn't sign in: {e}\n")
        print("Too many failed attempts.")
        return False

    # ---------- speech out ----------

    def say(self, text):
        """Speak out loud. A fresh engine each time — reusing one engine
        across a loop is a known way for pyttsx3 to go silent."""
        print(f"Alpha: {text}")
        try:
            engine = pyttsx3.init()
            engine.setProperty("rate", 175)
            engine.say(text)
            engine.runAndWait()
            engine.stop()
        except Exception as e:
            print(f"   [speech failed: {e}]")

    # ---------- speech in ----------

    def listen(self):
        """Record until you stop talking, rather than for a fixed time.

        Listens in short blocks, measures how loud each one is, and stops
        once there's been a moment of quiet after you've spoken. The
        threshold is calibrated against your actual background noise, so
        it works in a quiet room or a noisy one."""
        if not VOICE_INPUT_AVAILABLE:
            print("   [voice input not installed]")
            return ""

        block = int(SAMPLE_RATE * BLOCK_SECONDS)
        chunks = queue.Queue()

        def callback(indata, frames, time_info, status):
            chunks.put(indata.copy())

        collected = []
        speaking = False
        quiet_for = 0.0
        waited = 0.0
        elapsed = 0.0

        try:
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                dtype="int16", blocksize=block,
                                callback=callback):
                # Sample the room for a moment to learn what "quiet" means here.
                ambient = []
                for _ in range(4):
                    ambient.append(chunks.get())
                floor = float(np.sqrt(np.mean(
                    np.concatenate(ambient).astype(np.float32) ** 2)))
                threshold = max(floor * 3.0, 350.0)

                print("\nListening — speak now...")

                while True:
                    data = chunks.get()
                    level = float(np.sqrt(np.mean(data.astype(np.float32) ** 2)))

                    if not speaking:
                        waited += BLOCK_SECONDS
                        if level > threshold:
                            speaking = True
                            collected.append(data)
                        elif waited >= MAX_WAIT_TO_START:
                            print("   [heard nothing]")
                            return ""
                        continue

                    collected.append(data)
                    elapsed += BLOCK_SECONDS

                    if level <= threshold:
                        quiet_for += BLOCK_SECONDS
                        if quiet_for >= SILENCE_TO_STOP:
                            break
                    else:
                        quiet_for = 0.0

                    if elapsed >= MAX_RECORD_SECONDS:
                        break
        except Exception as e:
            print(f"   [microphone error: {e}]")
            return ""

        if not collected:
            return ""

        print("Thinking...")
        recording = np.concatenate(collected)

        try:
            audio = sr.AudioData(recording.tobytes(), SAMPLE_RATE, 2)
            text = self.recognizer.recognize_google(audio)
            print(f"You said: {text}")
            return text
        except sr.UnknownValueError:
            print("   [couldn't make that out]")
            return ""
        except Exception as e:
            print(f"   [transcription error: {e}]")
            return ""

    # ---------- understanding ----------

    def interpret(self, instruction):
        """Send the conversation so far plus the new instruction, and get
        back a structured command."""
        messages = list(self.history)
        messages.append({"role": "user", "content": instruction})

        try:
            resp = requests.post(
                f"{PROXY_URL}/chat",
                headers={"Authorization": f"Bearer {self.token}"},
                json={
                    "system": INSTRUCTION,
                    "messages": messages,
                    "max_tokens": 500,
                    "model": MODEL,
                },
                timeout=90,
            )
        except requests.RequestException as e:
            return {"action": "say", "speech": f"I couldn't reach the server. {e}"}

        if resp.status_code == 403:
            return {
                "action": "say",
                "speech": "Alpha Desktop is a premium feature. "
                          "Upgrade your account to use it.",
            }
        if resp.status_code == 401:
            return {
                "action": "say",
                "speech": "Your session expired. Restart Alpha and sign in again.",
            }
        if resp.status_code != 200:
            return {"action": "say", "speech": "The server had a problem."}

        raw = resp.json().get("text", "").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Not valid JSON — treat whatever came back as a spoken answer
            # rather than losing it.
            return {"action": "say", "speech": raw or "I didn't understand that one."}

    # ---------- doing ----------

    def execute(self, cmd):
        action = cmd.get("action", "say")
        speech = cmd.get("speech", "")

        if action in NEEDS_CONFIRMATION:
            answer = input(f"   Confirm '{action}'? (yes/no): ").strip().lower()
            if answer not in ("yes", "y"):
                self.say("Cancelled.")
                return

        if speech:
            self.say(speech)

        try:
            if action == "open_app":
                self._open_app(cmd.get("app", ""))

            elif action == "open_website":
                webbrowser.open(cmd.get("url", ""))

            elif action == "search_web":
                q = urllib.parse.quote(cmd.get("query", ""))
                webbrowser.open(f"https://www.google.com/search?q={q}")

            elif action == "type_text":
                pyautogui.write(cmd.get("text", ""), interval=0.02)

            elif action == "press_keys":
                keys = cmd.get("keys", [])
                if keys:
                    pyautogui.hotkey(*keys)

            elif action == "screenshot":
                path = os.path.join(os.path.expanduser("~"), "Desktop", "alpha_shot.png")
                pyautogui.screenshot(path)
                self.say("Saved to your desktop.")

            elif action == "volume":
                direction = cmd.get("direction", "up")
                key = {
                    "up": "volumeup",
                    "down": "volumedown",
                    "mute": "volumemute",
                }.get(direction, "volumeup")
                presses = 1 if key == "volumemute" else 5
                for _ in range(presses):
                    pyautogui.press(key)

            elif action == "sleep":
                subprocess.run(
                    ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                    shell=True,
                )

            elif action == "lock":
                subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"], shell=True)

            elif action == "shutdown":
                subprocess.run(["shutdown", "/s", "/t", "5"], shell=True)

            elif action == "restart":
                subprocess.run(["shutdown", "/r", "/t", "5"], shell=True)

        except Exception as e:
            self.say(f"That didn't work: {e}")

    def _open_app(self, name):
        name = name.lower().strip()
        command = KNOWN_APPS.get(name, name)
        try:
            os.startfile(command)
        except Exception:
            try:
                subprocess.Popen(command, shell=True)
            except Exception:
                self.say(f"I couldn't find {name} on this machine.")

    # ---------- main loop ----------

    def get_instruction(self):
        if self.voice_input:
            return self.listen()
        try:
            return input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            return "quit"

    def wake_proxy(self):
        """The free tier sleeps after 15 minutes idle and takes ~50 seconds
        to wake. Poke it in the background during sign-in so that wait
        happens while you're typing, not on your first command."""
        def ping():
            try:
                requests.get(f"{PROXY_URL}/health", timeout=90)
            except Exception:
                pass
        threading.Thread(target=ping, daemon=True).start()

    def run(self):
        self.wake_proxy()

        if not self.sign_in():
            return

        if self.history:
            print(f"(Carrying on from last time — {len(self.history)} messages remembered.)")
        self.say("Alpha online.")
        if not VOICE_INPUT_AVAILABLE:
            print("(Voice input isn't installed — typing only for now.)")

        while True:
            instruction = self.get_instruction()
            if not instruction:
                continue

            lowered = instruction.lower().strip(" .")

            if lowered in ("quit", "exit", "stop", "goodbye"):
                self.say("Goodbye.")
                break

            if lowered in ("forget", "forget everything", "new chat", "clear memory"):
                self.forget()
                self.say("Memory cleared.")
                continue

            if lowered == "voice":
                if VOICE_INPUT_AVAILABLE:
                    self.voice_input = True
                    self.say("Listening mode on. Say 'text mode' to go back.")
                else:
                    self.say("Voice input isn't installed yet.")
                continue

            if lowered in ("text", "text mode"):
                self.voice_input = False
                self.say("Typing mode on.")
                continue

            cmd = self.interpret(instruction)
            self.execute(cmd)

            # Keep the thread of the conversation for next time.
            self.remember("user", instruction)
            self.remember("assistant", cmd.get("speech", ""))


if __name__ == "__main__":
    Alpha().run()