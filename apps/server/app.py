from flask import Flask


app = Flask(__name__)


@app.get("/")
def index() -> str:
    return "Hello Flask!"
