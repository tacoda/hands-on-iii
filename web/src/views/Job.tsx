import { useParams } from "react-router-dom";
import { usePoll } from "../poll";
import type { Entry } from "../types";

// One job's entries from the record, in order (audit::read with this subject).
export function Job() {
  const { id = "" } = useParams();
  const { data, error } = usePoll<{ entries: Entry[] }>(`/job?id=${encodeURIComponent(id)}`);
  if (error) return <p role="alert">Cannot load job {id}: {error}</p>;
  if (!data) return <p>Loading</p>;
  return (
    <>
      <h2>Job <code>{id}</code></h2>
      <table>
        <thead><tr><th>When</th><th>Kind</th><th>Actor</th><th>Detail</th></tr></thead>
        <tbody>
          {data.entries.map((e, i) => (
            <tr key={i}>
              <td>{e.at ?? ""}</td>
              <td><code>{e.kind}</code></td>
              <td>{e.actor}</td>
              <td><pre>{e.detail === undefined ? "" : JSON.stringify(e.detail, null, 2)}</pre></td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
