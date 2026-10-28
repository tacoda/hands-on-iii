import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

// The factory refuses a repo that FACTORY_REPOS does not list, and says so.
export function Submit() {
  const [spec, setSpec] = useState("specs/document-the-ports.md");
  const [repo, setRepo] = useState("tests/fixtures/sample-repo");
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setJobId(null);
    setError(null);
    api<{ job_id: string }>("/jobs", { spec, repo })
      .then((r) => setJobId(r.job_id), (err) => setError(err.message));
  };
  return (
    <form onSubmit={submit}>
      <label>Spec <input value={spec} onChange={(e) => setSpec(e.target.value)} /></label>
      <label>Repo <input value={repo} onChange={(e) => setRepo(e.target.value)} /></label>
      <button type="submit" disabled={!spec.trim() || !repo.trim()}>Submit</button>
      {jobId && <p>Started job <Link to={`/jobs/${jobId}`}>{jobId}</Link>.</p>}
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
