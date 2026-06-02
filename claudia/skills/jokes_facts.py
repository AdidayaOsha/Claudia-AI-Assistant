import random

from skills import Skill

JOKES = [
    ("Why do Java developers wear glasses?", "Because they don't C#."),
    ("I told my computer I needed a break.", "Now it won't stop sending me Kit-Kat ads."),
    ("Why was the JavaScript developer sad?", "Because he didn't Node how to Express himself."),
    ("A SQL query walks into a bar, walks up to two tables and asks:", "'Can I join you?'"),
    ("Why do programmers prefer dark mode?", "Because light attracts bugs."),
    ("How many programmers does it take to change a light bulb?", "None — that's a hardware problem."),
    ("Why did the developer go broke?", "Because he used up all his cache."),
    ("What do you call a fish without eyes?", "A fsh."),
    ("I asked my AI assistant to tell me a joke.", "It gave me a recursion error."),
    ("Why don't scientists trust atoms?", "Because they make up everything."),
]

FACTS = [
    "The first computer bug was an actual bug — a moth found in a Harvard Mark II relay in 1947.",
    "The first 1GB hard drive, released in 1980, weighed 550 pounds and cost $40,000.",
    "Indonesia has more active volcanoes than any other country on Earth — 127.",
    "A group of flamingos is called a flamboyance.",
    "Honey never spoils — edible honey was found in 3,000-year-old Egyptian tombs.",
    "The average person spends 6 months of their lifetime waiting for red lights.",
    "Antarctica is the world's largest desert.",
    "Light from the Sun takes exactly 8 minutes and 20 seconds to reach Earth.",
    "The human brain generates about 20 watts of electricity — enough to power a dim light bulb.",
    "Python was named after Monty Python, not the snake.",
]


class JokesFactsSkill(Skill):
    name = "jokes_facts"
    triggers = ["tell me a joke", "tell a joke", "say a joke", "make me laugh", "random fact", "fun fact", "give me a fact", "trivia", "did you know"]
    description = "Delivers jokes and trivia from a curated built-in collection."

    def __init__(self, config: dict):
        pass

    def execute(self, params: dict) -> str:
        raw = params.get("raw_input", "").lower()
        if any(w in raw for w in ("fact", "trivia", "interesting", "did you know")):
            return random.choice(FACTS)
        setup, punchline = random.choice(JOKES)
        return f"{setup} {punchline}"


if __name__ == "__main__":
    skill = JokesFactsSkill({})
    for _ in range(3):
        print(skill.execute({"raw_input": "tell me a joke"}))
    for _ in range(3):
        print(skill.execute({"raw_input": "give me a fact"}))
