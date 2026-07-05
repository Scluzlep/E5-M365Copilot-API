"""Command-line entry point.

    python -m copilot login        # interactive sign-in, persists the session
    python -m copilot ask "hi"     # one-shot completion via the pure-HTTP driver
"""

import sys


def main(argv) -> int:
    args = argv[:]
    
    # Parse backward compatible `login [session-name]`
    if args and args[0] == "login" and len(args) > 1 and not args[1].startswith("-"):
        session_name = args[1]
        session_dir = f"sessions/{session_name}"
        del args[1]
    else:
        session_dir = "session"
        
    if "--session" in args:
        idx = args.index("--session")
        if idx + 1 < len(args):
            session_dir = args[idx + 1]
            del args[idx:idx + 2]

    vnc_mode = False
    if "--vnc" in args:
        vnc_mode = True
        args.remove("--vnc")

    cmd = args[0] if args else "ask"
    if cmd == "login":
        import os
        
        # Environmental detection for Linux
        if sys.platform.startswith("linux") and not vnc_mode:
            if not os.environ.get("DISPLAY"):
                ans = input("No DISPLAY detected. Do you want to use the built-in VNC server (DISPLAY=:99)? [Y/n]: ")
                if ans.lower() != 'n':
                    vnc_mode = True
                    
        if vnc_mode:
            print("Launching in VNC mode on DISPLAY=:99 ...")
            os.environ["DISPLAY"] = ":99"

        # The browser is used only for interactive sign-in / token capture.
        from .browser import BrowserCopilot
        
        # For legacy "session", profile is session/profile. For "sessions/foo", profile is sessions/foo/profile.
        profile_path = os.path.join(session_dir, "profile")
        token_path = os.path.join(session_dir, "token.json")
        BrowserCopilot(profile_dir=profile_path, headless=False).login(path=token_path)
        return 0
    if cmd == "ask":
        prompt = " ".join(args[1:]) or "Hello!"
        from .client import CopilotClient

        for chunk in CopilotClient(session_dir=session_dir).stream(prompt):
            if isinstance(chunk, str):
                print(chunk, end="", flush=True)
            elif hasattr(chunk, "url"):
                print(f"\n[Image: {chunk.url}]", end="", flush=True)
        print()
        return 0
    print(f"Unknown command: {cmd!r}. Use 'login [--session dir]' or 'ask [--session dir] <prompt>'.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
