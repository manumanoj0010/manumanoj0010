"""Weekly GitHub -> Airtable application inventory demo.
Requires GITHUB_TOKEN, AIRTABLE_TOKEN, AIRTABLE_BASE_ID.
Uses the existing Applications table; does not overwrite manually maintained owners.
"""
import os, json, base64, time
import requests

GH = "https://api.github.com"
BASE = os.environ["AIRTABLE_BASE_ID"]
AT = "https://api.airtable.com/v0/" + BASE + "/Applications"
OWNER = os.environ.get("GITHUB_OWNER", "manumanoj0010")
gh = requests.Session()
gh.headers.update({"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"], "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
at = requests.Session()
at.headers.update({"Authorization": "Bearer " + os.environ["AIRTABLE_TOKEN"]})

def get(url, session=gh, params=None):
    r = session.get(url, params=params, timeout=30)
    if r.status_code in (403, 404):
        return None
    r.raise_for_status()
    return r.json()

def repos():
    page = 1
    while True:
        data = get(GH + "/user/repos", params={"per_page":100,"page":page,"affiliation":"owner,collaborator,organization_member"})
        if data is None: raise RuntimeError("Cannot list repositories")
        for item in data:
            yield item
        if len(data) < 100: break
        page += 1

def manifest(repo, branch):
    # Demo: root package.json only; monorepos need recursive manifest discovery.
    data = get(GH + f"/repos/{repo}/contents/package.json", params={"ref":branch})
    if not data or not data.get("content"): return {}
    try: return json.loads(base64.b64decode(data["content"]).decode())
    except (ValueError, UnicodeDecodeError): return {}

def alerts(repo, endpoint):
    counts = {"critical":0,"high":0,"medium":0,"low":0}
    page = 1
    while True:
        url = GH + f"/repos/{repo}/{endpoint}"
        data = get(url, params={"state":"open","per_page":100,"page":page})
        if data is None: return None
        for alert in data:
            severity = (alert.get("security_vulnerability") or {}).get("severity") or (alert.get("rule") or {}).get("security_severity_level")
            if severity in counts: counts[severity] += 1
        if len(data) < 100: break
        page += 1
    return counts

def airtable_records():
    out = {}
    offset = None
    while True:
        params = {"pageSize":100}
        if offset: params["offset"] = offset
        data = get(AT, at, params)
        if data is None: raise RuntimeError("Cannot read Airtable")
        for rec in data["records"]:
            url = rec["fields"].get("GitHub Repository")
            if url: out[url.rstrip("/")] = rec["id"]
        offset = data.get("offset")
        if not offset: return out

def write_batch(records, existing):
    for i in range(0, len(records), 10):
        chunk = records[i:i+10]
        creates, updates = [], []
        for rec in chunk:
            url = rec["fields"]["GitHub Repository"].rstrip("/")
            if url in existing:
                updates.append({"id":existing[url], **rec})
            else:
                creates.append(rec)
        for method, batch in [(at.patch,updates),(at.post,creates)]:
            if not batch: continue
            resp = method(AT, json={"records":batch,"typecast":True}, timeout=30)
            resp.raise_for_status()
        time.sleep(0.25)

def main():
    existing = airtable_records()
    rows = []
    for r in repos():
        full = r["full_name"]
        if r["owner"]["login"].lower() != OWNER.lower(): continue
        if r["name"] == OWNER: continue  # skip profile README repo
        branch = r["default_branch"]
        package = manifest(full, branch)
        deps = {**package.get("dependencies",{}),**package.get("devDependencies",{})}
        tech = []
        for name,label in [("@angular/core","Angular"),("react","React"),("express","Express"),("next","Next.js"),("mongoose","MongoDB/Mongoose")]:
            if name in deps: tech.append(label)
        security = alerts(full, "dependabot/alerts")
        status = ("Dependabot unavailable/not enabled" if security is None else "Dependabot open: " + str(sum(security.values())) + " (" + ", ".join(f"{k}:{v}" for k,v in security.items()) + ")")
        version = package.get("version")
        fields = {"Application Name":r["name"],"GitHub Repository":r["html_url"],"Default Branch":branch,"GitHub Visibility":"private" if r["private"] else "public","Sync Status":status}
        if tech: fields["Technologies"] = tech
        if version: fields["Current Version"] = version
        rows.append({"fields":fields})
    write_batch(rows, existing)
    print(f"Processed {len(rows)} repositories")

if __name__ == "__main__":
    main()
