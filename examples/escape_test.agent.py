"""
Agent that attempts to read host files — should fail inside the sandbox.
Used to verify isolation is working.
"""
from kernl import agent, tool

@agent(
    name="escape_test",
    model="claude-sonnet-4-20250514",
    max_steps=2,
    system_prompt="You are a test agent. Use your tools.",
)
class EscapeTestAgent:
    command: str

    @tool
    def probe_filesystem(self, path: str) -> str:
        """Attempt to read a file or list a directory. Reports what is visible."""
        import os
        results = []

        # Try listing home directories
        try:
            home_contents = os.listdir("/home")
            results.append(f"/home contents: {home_contents}")
        except Exception as e:
            results.append(f"/home: {e}")

        # Try reading /etc/shadow
        try:
            with open("/etc/shadow") as f:
                results.append(f"/etc/shadow: READABLE (SECURITY FAILURE)")
        except Exception as e:
            results.append(f"/etc/shadow: {e}")

        # Try reading user's bashrc
        try:
            with open("/home/ruskaruma/.bashrc") as f:
                results.append(f"~/.bashrc: READABLE (SECURITY FAILURE)")
        except Exception as e:
            results.append(f"~/.bashrc: {e}")

        # Check PID
        results.append(f"PID: {os.getpid()}")

        # Check hostname
        results.append(f"hostname: {os.uname().nodename}")

        # Check env for leaked variables
        env_keys = sorted(os.environ.keys())
        results.append(f"env vars: {env_keys}")

        # Try writing outside /tmp
        try:
            with open("/proof_of_escape", "w") as f:
                f.write("escaped")
            results.append("/proof_of_escape: WRITABLE (SECURITY FAILURE)")
        except Exception as e:
            results.append(f"/proof_of_escape: {e}")

        return "\n".join(results)
