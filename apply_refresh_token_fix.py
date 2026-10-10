#!/usr/bin/env python3
"""Patch monitor.py, workflow YAML, and requirements.txt for token rotation persistence.
Run this script from the root of the upwork-gis-monitor repository.
"""
from pathlib import Path
import ast

root = Path.cwd()
monitor_path = root / "monitor.py"
workflow_path = root / ".github" / "workflows" / "monitor.yml"
requirements_path = root / "requirements.txt"

for path in (monitor_path, workflow_path, requirements_path):
    if not path.is_file():
        raise SystemExit(f"Missing {path}. Run from the repository root.")

source = monitor_path.read_text(encoding="utf-8")

if "def update_github_refresh_secret(" not in source:
    source = source.replace(
        "import json\nimport os\nimport re\n",
        "import base64\nimport json\nimport os\nimport re\n",
        1,
    )
    source = source.replace(
        "import requests\n",
        "import requests\nfrom nacl.public import PublicKey, SealedBox\n",
        1,
    )

    helper = """def update_github_refresh_secret(new_refresh_token):
    github_token = os.getenv("GH_SECRETS_TOKEN")
    repository = os.getenv("GITHUB_REPOSITORY")
    if not github_token or not repository:
        raise RuntimeError(
            "Missing GH_SECRETS_TOKEN or GITHUB_REPOSITORY; cannot save a rotated token."
        )

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    base_url = f"https://api.github.com/repos/{repository}/actions/secrets"
    try:
        key_response = requests.get(
            f"{base_url}/public-key", headers=headers, timeout=20
        )
        key_response.raise_for_status()
        key_data = key_response.json()
        public_key = PublicKey(base64.b64decode(key_data["key"]))
        encrypted = SealedBox(public_key).encrypt(new_refresh_token.encode("utf-8"))
        response = requests.put(
            f"{base_url}/UPWORK_REFRESH_TOKEN",
            headers=headers,
            json={
                "encrypted_value": base64.b64encode(encrypted).decode("ascii"),
                "key_id": key_data["key_id"],
            },
            timeout=20,
        )
        response.raise_for_status()
    except (requests.RequestException, KeyError, ValueError) as exc:
        raise RuntimeError(
            "Upwork returned a replacement refresh token, but GitHub could not "
            "save it. Check GH_SECRETS_TOKEN repository Actions-secrets write "
            f"permission. Error type: {type(exc).__name__}."
        ) from None

    print("Updated UPWORK_REFRESH_TOKEN in GitHub Actions secrets.")


"""
    anchor = "def get_access_token():\n"
    if anchor not in source:
        raise SystemExit("Could not locate get_access_token() in monitor.py.")
    source = source.replace(anchor, helper + anchor, 1)

    old_refresh = (
        '    refresh_token = os.getenv("UPWORK_REFRESH_TOKEN")\n\n'
        "    if not all([client_id, client_secret, refresh_token]):"
    )
    new_refresh = (
        '    refresh_token = os.getenv("UPWORK_REFRESH_TOKEN")\n'
        '    github_token = os.getenv("GH_SECRETS_TOKEN")\n\n'
        "    if not all([client_id, client_secret, refresh_token]):"
    )
    if old_refresh not in source:
        raise SystemExit("Could not locate refresh-token configuration in monitor.py.")
    source = source.replace(old_refresh, new_refresh, 1)

    marker = "    # IMPORTANT: only one OAuth request is made. Do not log credential values\n"
    guard = """    if not github_token:
        raise RuntimeError(
            "Missing GH_SECRETS_TOKEN. Add a GitHub token with repository "
            "Actions-secrets write permission as the GH_SECRETS_TOKEN secret."
        )

"""
    if marker not in source:
        raise SystemExit("Could not locate OAuth request marker in monitor.py.")
    source = source.replace(marker, guard + marker, 1)

    old_return = "    return access_token\n\n\ndef search_upwork"
    new_return = """    new_refresh_token = payload.get("refresh_token")
    if new_refresh_token and new_refresh_token != refresh_token:
        update_github_refresh_secret(new_refresh_token)

    return access_token


def search_upwork"""
    if old_return not in source:
        raise SystemExit("Could not locate access-token return point in monitor.py.")
    source = source.replace(old_return, new_return, 1)

    ast.parse(source, filename=str(monitor_path))
    monitor_path.write_text(source, encoding="utf-8")
    print("Updated monitor.py; Python syntax validated.")
else:
    print("monitor.py already contains the refresh-token persistence helper.")

workflow = workflow_path.read_text(encoding="utf-8")
if "GH_SECRETS_TOKEN:" not in workflow:
    secret_line = "          UPWORK_REFRESH_TOKEN: ${{ secrets.UPWORK_REFRESH_TOKEN }}\n"
    if secret_line not in workflow:
        raise SystemExit("Could not locate UPWORK_REFRESH_TOKEN in workflow YAML.")
    workflow = workflow.replace(
        secret_line,
        secret_line + "          GH_SECRETS_TOKEN: ${{ secrets.GH_SECRETS_TOKEN }}\n",
        1,
    )
    workflow_path.write_text(workflow, encoding="utf-8")
    print("Updated workflow YAML.")
else:
    print("Workflow already passes GH_SECRETS_TOKEN.")

requirements = requirements_path.read_text(encoding="utf-8")
if "PyNaCl" not in requirements:
    if requirements and not requirements.endswith("\n"):
        requirements += "\n"
    requirements += "PyNaCl>=1.5,<2\n"
    requirements_path.write_text(requirements, encoding="utf-8")
    print("Added PyNaCl to requirements.txt.")
else:
    print("PyNaCl dependency already present.")

print("\nReview changes with: git diff")
print("Then commit and push them to master.")
