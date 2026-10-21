"""HTTP metadata probe using only the Python standard library."""
import json
import re
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main(params: dict) -> None:
    url = str(params["url"])
    if not url.startswith(("http://", "https://")):
        print("url must start with http:// or https://", file=sys.stderr)
        sys.exit(2)
    timeout = int(params.get("timeout", 10))
    opener = urllib.request.build_opener() if params.get("follow_redirects", True) else \
        urllib.request.build_opener(NoRedirect())
    req = urllib.request.Request(url, headers={"User-Agent": "secbox-http-probe/1.0"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read(256_000).decode("utf-8", "replace")
            headers = dict(resp.headers.items())
            title = re.search(r"<title[^>]*>\s*(.*?)\s*</title>", body, re.I | re.S)
            security_headers = {key: headers[key] for key in headers if key.lower() in {
                "strict-transport-security", "content-security-policy", "x-frame-options",
                "x-content-type-options", "referrer-policy", "permissions-policy"}}
            print(json.dumps({"status": resp.status, "url": resp.url, "headers": headers,
                              "title": title.group(1) if title else "",
                              "security_headers": security_headers}, ensure_ascii=False, indent=2))
    except urllib.error.HTTPError as e:
        print(json.dumps({"status": e.code, "url": e.url, "headers": dict(e.headers.items()),
                          "detail": str(e)}, ensure_ascii=False, indent=2))
    except urllib.error.URLError as e:
        print(f"request failed: {e.reason}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
