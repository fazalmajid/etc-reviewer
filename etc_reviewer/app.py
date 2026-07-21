from __future__ import annotations

import os

from flask import Flask, jsonify, request, send_from_directory

from . import ssh_ops

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def create_app(machines_file: str) -> Flask:
    app = Flask(__name__, static_folder=None)

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/app.js")
    def app_js():
        return send_from_directory(STATIC_DIR, "app.js")

    @app.get("/style.css")
    def style_css():
        return send_from_directory(STATIC_DIR, "style.css")

    @app.get("/api/status")
    def api_status():
        try:
            hosts = ssh_ops.read_machines(machines_file)
        except OSError as exc:
            return jsonify({"error": f"Could not read machines file: {exc}"}), 500

        statuses = ssh_ops.fetch_all_status(hosts)
        return jsonify({
            "machines": [
                {
                    "host": s.host,
                    "reachable": s.reachable,
                    "hostname": s.hostname,
                    "os": s.os,
                    "error": s.error,
                    "changes": s.changes,
                }
                for s in statuses
            ]
        })

    @app.post("/api/diff")
    def api_diff():
        data = request.get_json(force=True, silent=True) or {}
        host = data.get("host")
        path = data.get("path")
        untracked = bool(data.get("untracked"))
        if not host or not path:
            return jsonify({"error": "host and path are required"}), 400
        diff_text, error = ssh_ops.get_diff(host, path, untracked)
        if error:
            return jsonify({"error": error}), 502
        return jsonify({"diff": diff_text})

    @app.post("/api/commit")
    def api_commit():
        data = request.get_json(force=True, silent=True) or {}
        message = (data.get("message") or "").strip()
        selections = data.get("selections") or {}
        if not message:
            return jsonify({"error": "Commit message is required"}), 400
        if not selections:
            return jsonify({"error": "No files selected"}), 400
        results = ssh_ops.commit_all(selections, message)
        return jsonify({"results": results})

    return app
