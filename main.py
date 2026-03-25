import os
from dotenv import load_dotenv
from app import create_app

load_dotenv(".secrets.env")  # no-op in Docker (file excluded by .dockerignore); loads for local dev

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", debug=False, port=port)
