from app import app

if __name__ == "__main__":
    import os

    app.run(debug=os.getenv("FLASK_DEBUG") == "1")
