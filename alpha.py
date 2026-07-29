import ollama

# The personality/instructions for TheAlpha AI
SYSTEM_PROMPT = "You are TheAlpha AI, a helpful and concise assistant."

# Send a message to the model
response = ollama.chat(
    model="llama3.2",
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Hello, who are you?"}
    ]
)

# Print what the model said back
print(response["message"]["content"])