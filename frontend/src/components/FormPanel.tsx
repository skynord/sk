import type { FormStats, TeamFormBundle } from "../api/types";
import { num, pct } from "../lib/format";

function WDL({ form }: { form: FormStats }) {
  return (
    <span className="wdl">
      {form.wins > 0 && <span className="w">{form.wins}W</span>}
      {form.draws > 0 && <span className="d">{form.draws}D</span>}
      {form.losses > 0 && <span className="l">{form.losses}L</span>}
    </span>
  );
}

function FormColumns({ form }: { form: FormStats }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Window</th>
          <th>Form</th>
          <th className="num">GF</th>
          <th className="num">GA</th>
          <th className="num">Pts/match</th>
          <th className="num">BTTS</th>
        </tr>
      </thead>
      <tbody>
        {(["last5", "last10", "last15"] as const).map((key) => {
          const window = key.replace("last", "");
          return (
            <tr key={key}>
              <td>{`Last ${window}`}</td>
              <td>
                <WDL form={form} />
              </td>
              <td className="num">{form.goals_for}</td>
              <td className="num">{form.goals_against}</td>
              <td className="num">{num(form.points_per_match)}</td>
              <td className="num">{form.btts_frequency !== null ? pct(form.btts_frequency, 0) : "–"}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function FormPanel({ bundle }: { bundle: TeamFormBundle }) {
  const overall = bundle.last15;
  return (
    <div>
      <FormColumns form={overall} />
      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <div className="stat">
          <div className="label">Home split (last 15)</div>
          <div className="row" style={{ marginTop: 4 }}>
            <WDL form={bundle.splits.home} />
            <span className="muted">
              {num(bundle.splits.home.avg_goals_for)} GF / {num(bundle.splits.home.avg_goals_against)} GA
            </span>
          </div>
        </div>
        <div className="stat">
          <div className="label">Away split (last 15)</div>
          <div className="row" style={{ marginTop: 4 }}>
            <WDL form={bundle.splits.away} />
            <span className="muted">
              {num(bundle.splits.away.avg_goals_for)} GF / {num(bundle.splits.away.avg_goals_against)} GA
            </span>
          </div>
        </div>
      </div>
      <div className="row" style={{ marginTop: 12 }}>
        <span className="badge">Rest days: {bundle.schedule.rest_days ?? "–"}</span>
        <span className="badge">Matches last 14d: {bundle.schedule.matches_last_14_days}</span>
        {bundle.xg.n_with_xg > 0 ? (
          <span className="badge ok">
            xG: {num(bundle.xg.xg_for_avg)} for / {num(bundle.xg.xg_against_avg)} against
          </span>
        ) : (
          <span className="badge warn">No xG data</span>
        )}
      </div>
      {overall.notes.length > 0 && (
        <p className="faint" style={{ fontSize: 12, marginBottom: 0 }}>
          {overall.notes.join("; ")}
        </p>
      )}
    </div>
  );
}
