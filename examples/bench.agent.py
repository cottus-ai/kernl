"""
Benchmark agent — does a small amount of compute in tools.
Used to measure spawn/execution overhead, not LLM latency.
"""
from kernl import agent, tool

@agent(
    name="bench",
    model="claude-sonnet-4-20250514",
    max_steps=3,
    system_prompt="You are a benchmark agent. Use your tools.",
)
class BenchAgent:
    input_data: str

    @tool
    def compute(self, n: str) -> str:
        """Do a small computation to simulate tool work."""
        total = 0
        for i in range(int(n)):
            total += i * i
        return str(total)

    @tool
    def echo(self, message: str) -> str:
        """Echo back a message."""
        return message
