import os

from app import app


if __name__ == "__main__":
    host = app.config["HOST"]
    port = int(os.environ.get("PORT", "5000"))

    if app.config["ENVIRONMENT"] == "production":
        from waitress import serve

        serve(app, host=host, port=port)
    else:
        app.run(host=host, port=port, debug=app.config["DEBUG"])
