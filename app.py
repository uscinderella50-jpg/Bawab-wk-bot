import os

from flask import Flask

app = Flask(__name__)


@app.route("/")
def home():
    return """
<!DOCTYPE html>
<html lang="en">
<body style="background:#0d0d0d;color:#fff;font-family:monospace;text-align:center;padding:60px">
    <h2 style="color:#e040fb;">👑 Bawaab Wk Bot — Running ✅</h2>
    <p style="color:#aaa;">PDF Watermarking Service</p>
</body>
</html>
"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
