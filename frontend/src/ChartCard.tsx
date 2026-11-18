// A chart under an answer, for numbers about many people ("average salary by department").
//
// The numbers come from the CRUD API (GET /analytics), passed on by the agent's code. The AI never
// writes them, so the chart is right even if the AI's text gets a number wrong.
//
// Drawn with Recharts (https://recharts.org), the one chart library this project uses.

import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Chart } from "./api";

type Props = {
  chart: Chart;
};

// The page's own accent blue (styles.css). One colour, because a chart here has one series.
const COLOR = "#2f5bd3";
const GRID = "#e5e7eb"; // light grey: the grid helps, but must not compete with the data
const TEXT = "#6b7280";

// 104325.5 -> "104,325.5" (the numbers have no currency, so we never add one)
function formatNumber(value: number): string {
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

export default function ChartCard({ chart }: Props) {
  const axisText = { fill: TEXT, fontSize: 12 };

  return (
    <figure className="chart-card">
      <figcaption>
        <strong>{chart.title}</strong>
        <span className="muted">{chart.details}</span>
      </figcaption>

      {chart.kind === "bar" ? (
        // Bars lie down (layout="vertical"), so long names like "Customer Support" stay readable.
        <ResponsiveContainer width="100%" height={chart.points.length * 32 + 40}>
          <BarChart data={chart.points} layout="vertical" margin={{ left: 8, right: 24 }}>
            <CartesianGrid horizontal={false} stroke={GRID} />
            <XAxis type="number" tick={axisText} tickFormatter={formatNumber} stroke={GRID} />
            <YAxis type="category" dataKey="label" width={130} tick={axisText} stroke={GRID} />
            <Tooltip formatter={(value: number) => formatNumber(value)} cursor={{ fill: "#f3f4f6" }} />
            {/* Thin bars with a rounded end, square where they start. */}
            <Bar dataKey="value" name="Value" fill={COLOR} barSize={18} radius={[0, 4, 4, 0]} />
          </BarChart>
        </ResponsiveContainer>
      ) : (
        // Over time (months or years): a line, oldest on the left.
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={chart.points} margin={{ left: 8, right: 24, top: 8 }}>
            <CartesianGrid vertical={false} stroke={GRID} />
            <XAxis dataKey="label" tick={axisText} stroke={GRID} />
            <YAxis tick={axisText} tickFormatter={formatNumber} stroke={GRID} width={70} />
            <Tooltip formatter={(value: number) => formatNumber(value)} />
            <Line dataKey="value" name="Value" stroke={COLOR} strokeWidth={2} dot={{ r: 4, fill: COLOR, strokeWidth: 0 }} />
          </LineChart>
        </ResponsiveContainer>
      )}

      {/* The same numbers as a table, for anyone who can't (or would rather not) read a chart. */}
      <details>
        <summary>Show the numbers</summary>
        <table>
          <tbody>
            {chart.points.map((point) => (
              <tr key={point.label}>
                <td>{point.label}</td>
                <td className="number">{formatNumber(point.value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}
