import subprocess
try:
    subprocess.run(["git", "push", "origin", "debug/health-check"], check=True)
    print("Push successful")
except Exception as e:
    print(f"Push failed: {e}")
