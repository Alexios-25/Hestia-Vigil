from flask import Flask

app = Flask(__name__)

@app.route("/")
def index():
    return "<h1>🔥 Hestia.Vigil</h1><p>FIRMS data loading soon...</p>"

if __name__ == "__main__":
    app.run(host='127.0.0.1', port=5001, debug=True)