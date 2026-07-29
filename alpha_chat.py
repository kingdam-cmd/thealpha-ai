import ollama

SYSTEM_PROMPT = "You are TheAlpha AI, a helpful and concise assistant."

# The conversation history — starts with just the system instructions
messages = [
    {"role": "system", "content": SYSTEM_PROMPT}
]

print("TheAlpha AI — type 'quit' to exit")
print("-" * 40)

while True:
    # Get input from the user
    user_input = input("\nYou: ")

    if user_input.lower() in ["quit", "exit", "bye"]:
        print("\nTheAlpha AI: Goodbye!")
        break

    # Add the user's message to the history
    messages.append({"role": "user", "content": user_input})

    # Send the ENTIRE history to the model
    response = ollama.chat(model="llama3.2", messages=messages)
    reply = response["message"]["content"]

    # Add the AI's reply to the history too
    messages.append({"role": "assistant", "content": reply})

    print(f"\nTheAlpha AI: {reply}")