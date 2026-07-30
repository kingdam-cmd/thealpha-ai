"""
Alpha — the desktop assistant for TheAlpha.

Runs on YOUR laptop. Takes a plain-English instruction, asks the AI what
it means, then carries it out on this machine.

This version holds no API key. It signs you in with your TheAlpha AI
account and sends requests through the Alpha proxy, which checks that
your account has premium access.

Run it with:  python alpha_desktop.py

While it's running:
  type an instruction   - carry it out
  type 'voice'          - switch to speaking your commands
  type 'text'           - switch back to typing
  type 'quit'           - stop
"""

import os
import json
import getpass
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

# Voice input is optional — Alpha still works by typing if these fail to import.
try:
    import speech_recognition as sr
    import sounddevice as sd
    import numpy as np
    VOICE_INPUT_AVAILABLE = True
except ImportError:
    VOICE_INPUT_AVAILABLE = False

SAMPLE_RATE = 16000
RECORD_SECONDS = 6

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

INSTRUCTION = """You are the command interpreter for Alpha, a desktop assistant
running on a Windows laptop. Read the user's instruction and reply with ONE
JSON object and nothing else. No markdown fences, no explanation.

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
{"action": "say", "text": "<reply>"}              - just answer conversationally

Rules:
- If the instruction is a question or chat rather than a command, use "say".
- Always include a short "speech" key with what Alpha should say out loud.
- For open_app, use the plain name (e.g. "notepad", "chrome", "spotify").

Example:
User: open notepad and write hello
{"action": "open_app", "app": "notepad", "speech": "Opening Notepad."}
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

    # ---------- signing in ----------

    def sign_in(self):
        """Ask for the same credentials used on TheAlpha AI website."""
        print("Sign in with your TheAlpha AI account.\n")
        for attempt in range(3):
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
        """Record a few seconds from the microphone and transcribe it."""
        if not VOICE_INPUT_AVAILABLE:
            print("   [voice input not installed]")
            return ""
        try:
            print(f"\nListening for {RECORD_SECONDS} seconds — speak now...")
            recording = sd.rec(
                int(RECORD_SECONDS * SAMPLE_RATE),
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
            )
            sd.wait()
            print("Thinking...")

            audio = sr.AudioData(recording.tobytes(), SAMPLE_RATE, 2)
            text = self.recognizer.recognize_google(audio)
            print(f"You said: {text}")
            return text
        except sr.UnknownValueError:
            print("   [couldn't make that out]")
            return ""
        except Exception as e:
            print(f"   [microphone error: {e}]")
            return ""

    # ---------- understanding ----------

    def interpret(self, instruction):
        """Ask the proxy to turn plain English into a structured command."""
        try:
            resp = requests.post(
                f"{PROXY_URL}/chat",
                headers={"Authorization": f"Bearer {self.token}"},
                json={
                    "system": INSTRUCTION,
                    "messages": [{"role": "user", "content": instruction}],
                    "max_tokens": 500,
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
            return {"action": "say", "speech": "I didn't understand that one."}

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

    def run(self):
        if not self.sign_in():
            return

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


if __name__ == "__main__":
    Alpha().run()