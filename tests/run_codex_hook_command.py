import json
import os
import subprocess


command = os.environ["CONTEXT_MEMORY_TEST_CODEX_COMMAND"]
payload = os.environ["CONTEXT_MEMORY_TEST_CODEX_PAYLOAD"]
powershell = os.path.join(
    os.environ["SystemRoot"],
    "System32",
    "WindowsPowerShell",
    "v1.0",
    "powershell.exe",
)


def run(argv):
    result = subprocess.run(
        argv,
        input=payload,
        text=True,
        capture_output=True,
    )
    return {
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


print(
    json.dumps(
        {
            "cmd": run([os.environ["ComSpec"], "/D", "/S", "/C", command]),
            "powershell": run(
                [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command]
            ),
        }
    )
)
