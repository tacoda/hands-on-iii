import { Link } from "react-router-dom";
import { usePoll } from "../poll";
import type { Job } from "../types";

// factory::jobs already sorts stuck jobs first, so the table keeps its order.
export function Jobs() {
  const { data, error } = usePoll<{ jobs: Job[] }>("/jobs");
  if (error) return <p role="alert">Cannot load jobs: {error}</p>;
  if (!data) return <p>Loading</p>;
  if (data.jobs.length === 0) return <p>No jobs. Submit a spec.</p>;
  return (
    <table>
      <thead><tr><th>Job</th><th>Stage</th><th>Epoch</th><th>Spec</th><th>Last reason</th></tr></thead>
      <tbody>
        {data.jobs.map((j) => (
          <tr key={j.id} className={j.stuck ? "stuck" : undefined}>
            <td><Link to={`/jobs/${j.id}`}>{j.id.slice(0, 8)}</Link></td>
            <td>{j.stage}{j.stuck && " (stuck)"}</td>
            <td>{j.epoch}</td>
            <td><code>{j.spec}</code></td>
            <td>{j.reason}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
