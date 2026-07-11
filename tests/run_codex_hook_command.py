import json
import os
import subprocess


result = subprocess.run(
    [
        os.environ["ComSpec"],
        "/D",
        "/S",
        "/C",
        os.environ["CONTEXT_MEMORY_TEST_CODEX_COMMAND"],
    ],
    input=os.environ["CONTEXT_MEMORY_TEST_CODEX_PAYLOAD"],
    text=True,
    capture_output=True,
)
print(
    json.dumps(
        {
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    )
)
