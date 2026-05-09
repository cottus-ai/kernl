"""
A research agent that looks up information and synthesizes answers.
Demonstrates multiple tools and multi-step reasoning.
"""
from akernl import agent, tool

@agent(
    name="researcher",
    model="claude-sonnet-4-20250514",
    max_steps=10,
    system_prompt="You are a research assistant. Use your tools to look up facts, then provide a clear, sourced answer.",
)
class ResearchAgent:
    query: str

    @tool
    def search_knowledge_base(self, topic: str) -> str:
        """Search the internal knowledge base for information on a topic."""
        kb = {
            "python": "Python is a high-level programming language created by Guido van Rossum in 1991. It emphasizes readability and supports multiple paradigms.",
            "rust": "Rust is a systems programming language focused on safety, speed, and concurrency. Created by Graydon Hoare at Mozilla, released in 2015.",
            "akernl": "Akernl is an experimental agent runtime that compiles agent definitions into minimal execution units for high-density deployment.",
            "firecracker": "Firecracker is a virtual machine monitor (VMM) created by AWS for serverless computing. It creates microVMs in ~125ms with minimal memory overhead.",
        }
        for key, value in kb.items():
            if key in topic.lower():
                return value
        return f"No results found for: {topic}"

    @tool
    def get_current_date(self) -> str:
        """Get the current date and time."""
        import datetime
        return datetime.datetime.now().isoformat()

    @tool
    def count_words(self, text: str) -> str:
        """Count the number of words in a text."""
        return str(len(text.split()))
