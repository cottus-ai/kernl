"""
A minimal Kernl agent that answers a question using Claude.
"""
from kernl import agent, tool

@agent(
    name="hello",
    model="claude-sonnet-4-20250514",
    max_steps=5,
    system_prompt="You are a helpful assistant. Be concise.",
)
class HelloAgent:
    question: str

    @tool
    def calculate(self, expression: str) -> str:
        """Evaluate a mathematical expression. Only supports basic arithmetic."""
        allowed = set("0123456789+-*/.() ")
        if not all(c in allowed for c in expression):
            return "ERROR: only basic arithmetic is allowed"
        try:
            result = eval(expression)  # safe: restricted character set
            return str(result)
        except Exception as e:
            return f"ERROR: {e}"

    @tool
    def lookup_constant(self, name: str) -> str:
        """Look up a mathematical or physical constant by name."""
        constants = {
            "pi": "3.14159265358979",
            "e": "2.71828182845905",
            "c": "299792458 m/s",
            "g": "9.80665 m/s^2",
            "avogadro": "6.02214076e23",
        }
        return constants.get(name.lower(), f"Unknown constant: {name}")
