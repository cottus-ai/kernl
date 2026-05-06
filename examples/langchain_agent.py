from langchain.tools import BaseTool


class SearchTool(BaseTool):
    name = "search"
    description = "Search the knowledge base for a topic"

    def _run(self, query: str) -> str:
        kb = {
            "firecracker": "Firecracker is a VMM by AWS for microVMs. <50ms cold start.",
            "unikernel": "Unikernels are single-address-space OS images with minimal attack surface.",
            "python": "Python is a high-level interpreted language with dynamic typing.",
        }
        for key, val in kb.items():
            if key in query.lower():
                return val
        return f"No results for: {query}"


class SummaryTool(BaseTool):
    name = "summarize"
    description = "Summarize a passage of text to its key points"

    def _run(self, text: str) -> str:
        words = text.split()
        return " ".join(words[:30]) + ("..." if len(words) > 30 else "")
