/**
 * DataSourceResultRenderer — renders query_data_source tool results with
 * table/chart tab switching. Uses recharts for visualization.
 */

import { useMemo, useState } from "react";
import { Tabs, Table, Empty, Typography, Tag } from "antd";
import {
  BarChart,
  Bar,
  LineChart,
  Line,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import { useTranslation } from "react-i18next";
import type { ToolRenderProps } from "../types";
import styles from "../../../pages/Chat/index.module.less";

const { Text } = Typography;

// Color palette for pie chart and multi-series
const CHART_COLORS = [
  "#1677ff",
  "#52c41a",
  "#faad14",
  "#f5222d",
  "#722ed1",
  "#13c2c2",
  "#eb2f96",
  "#fa8c16",
  "#a0d911",
  "#2f54eb",
];

type ChartType = "table" | "bar" | "line" | "pie";

interface DataSourceResultData {
  columns: string[];
  rows: unknown[][];
  row_count: number;
  status: string;
  generated_sql: string;
  spilled: boolean;
}

function isDataSourceData(data: unknown): data is DataSourceResultData {
  if (!data || typeof data !== "object") return false;
  const obj = data as Record<string, unknown>;
  return (
    Array.isArray(obj.columns) &&
    Array.isArray(obj.rows) &&
    typeof obj.row_count === "number"
  );
}

/**
 * Infer the best default chart type based on data shape.
 */
function inferDefaultChartType(data: DataSourceResultData): ChartType {
  const { columns, rows } = data;
  if (columns.length === 0 || rows.length === 0) return "table";

  // Count numeric columns
  const numericColIndices: number[] = [];
  for (let colIdx = 0; colIdx < columns.length; colIdx++) {
    const sampleValues = rows.slice(0, 5).map((r) => r[colIdx]);
    if (sampleValues.every((v) => typeof v === "number" || !isNaN(Number(v)))) {
      numericColIndices.push(colIdx);
    }
  }

  const categoricalColIndices = columns
    .map((_, i) => i)
    .filter((i) => !numericColIndices.includes(i));

  // Single categorical + single numeric → bar or pie
  if (categoricalColIndices.length === 1 && numericColIndices.length === 1) {
    return rows.length <= 8 ? "pie" : "bar";
  }

  // Single categorical + multiple numeric → bar (grouped)
  if (categoricalColIndices.length === 1 && numericColIndices.length > 1) {
    return "bar";
  }

  // Two numeric columns → line (scatter-like)
  if (numericColIndices.length === 2 && categoricalColIndices.length === 0) {
    return "line";
  }

  // Multiple rows with any numeric → line
  if (rows.length > 3 && numericColIndices.length > 0) {
    return "line";
  }

  return "table";
}

/**
 * Transform rows/columns into chart-friendly data format.
 */
function transformDataForChart(data: DataSourceResultData) {
  const { columns, rows } = data;
  if (columns.length === 0 || rows.length === 0) return { chartData: [], xKey: "", yKeys: [] };

  // Find categorical (x-axis) and numeric (y-axis) columns
  const numericColIndices: number[] = [];
  for (let colIdx = 0; colIdx < columns.length; colIdx++) {
    const sampleValues = rows.slice(0, 5).map((r) => r[colIdx]);
    if (sampleValues.every((v) => typeof v === "number" || !isNaN(Number(v)))) {
      numericColIndices.push(colIdx);
    }
  }

  const categoricalColIndices = columns
    .map((_, i) => i)
    .filter((i) => !numericColIndices.includes(i));

  // Use first categorical column as x-axis, or first column if no categorical
  const xKeyIdx = categoricalColIndices[0] ?? 0;
  const xKey = columns[xKeyIdx];
  const yKeys = numericColIndices.length > 0
    ? numericColIndices.map((i) => columns[i])
    : columns.filter((_, i) => i !== xKeyIdx);

  const chartData = rows.map((row) => {
    const point: Record<string, unknown> = {};
    point[xKey] = row[xKeyIdx];
    for (const yKey of yKeys) {
      const yIdx = columns.indexOf(yKey);
      const val = row[yIdx];
      point[yKey] = typeof val === "number" ? val : Number(val) || 0;
    }
    return point;
  });

  return { chartData, xKey, yKeys };
}

function DataTable({ data }: { data: DataSourceResultData }) {
  const { columns, rows } = data;

  const tableColumns = useMemo(
    () =>
      columns.map((col, idx) => ({
        title: col,
        dataIndex: String(idx),
        key: String(idx),
        ellipsis: true,
        render: (val: unknown) => {
          if (val === null || val === undefined) return <Text type="secondary">—</Text>;
          return String(val);
        },
      })),
    [columns],
  );

  const tableData = useMemo(
    () =>
      rows.map((row, rowIdx) => {
        const record: Record<string, unknown> = { key: String(rowIdx) };
        row.forEach((val, colIdx) => {
          record[String(colIdx)] = val;
        });
        return record;
      }),
    [rows],
  );

  return (
    <Table
      columns={tableColumns}
      dataSource={tableData}
      pagination={rows.length > 10 ? { pageSize: 10, size: "small" } : false}
      size="small"
      scroll={{ x: "max-content" }}
      className={styles.dataSourceTable}
    />
  );
}

function BarChartView({ data }: { data: DataSourceResultData }) {
  const { chartData, xKey, yKeys } = transformDataForChart(data);
  if (chartData.length === 0 || !xKey) return <Empty description="No data" />;

  return (
    <ResponsiveContainer width="100%" height={300}>
      <BarChart data={chartData}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis dataKey={xKey} tick={{ fontSize: 12 }} />
        <YAxis tick={{ fontSize: 12 }} />
        <RechartsTooltip />
        <Legend />
        {yKeys.map((yKey, idx) => (
          <Bar
            key={yKey}
            dataKey={yKey}
            fill={CHART_COLORS[idx % CHART_COLORS.length]}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

function LineChartView({ data }: { data: DataSourceResultData }) {
  const { chartData, xKey, yKeys } = transformDataForChart(data);
  if (chartData.length === 0 || !xKey) return <Empty description="No data" />;

  return (
    <ResponsiveContainer width="100%" height={300}>
      <LineChart data={chartData}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis dataKey={xKey} tick={{ fontSize: 12 }} />
        <YAxis tick={{ fontSize: 12 }} />
        <RechartsTooltip />
        <Legend />
        {yKeys.map((yKey, idx) => (
          <Line
            key={yKey}
            type="monotone"
            dataKey={yKey}
            stroke={CHART_COLORS[idx % CHART_COLORS.length]}
            strokeWidth={2}
            dot={{ r: 3 }}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

function PieChartView({ data }: { data: DataSourceResultData }) {
  const { chartData, xKey, yKeys } = transformDataForChart(data);
  if (chartData.length === 0 || !xKey || yKeys.length === 0) {
    return <Empty description="No data" />;
  }

  const yKey = yKeys[0];
  const pieData = chartData.map((d) => ({
    name: String(d[xKey]),
    value: Number(d[yKey]) || 0,
  }));

  return (
    <ResponsiveContainer width="100%" height={300}>
      <PieChart>
        <Pie
          data={pieData}
          dataKey="value"
          nameKey="name"
          cx="50%"
          cy="50%"
          outerRadius={100}
          label={(entry) => entry.name}
        >
          {pieData.map((_, idx) => (
            <Cell key={idx} fill={CHART_COLORS[idx % CHART_COLORS.length]} />
          ))}
        </Pie>
        <RechartsTooltip />
        <Legend />
      </PieChart>
    </ResponsiveContainer>
  );
}

/**
 * Main renderer component for data source query results.
 */
export function DataSourceResultRenderer({ data, textFallback }: ToolRenderProps) {
  const { t } = useTranslation();

  const resultData = useMemo(() => {
    if (isDataSourceData(data)) return data;
    return null;
  }, [data]);

  const defaultChartType = useMemo(() => {
    if (!resultData) return "table" as ChartType;
    return inferDefaultChartType(resultData);
  }, [resultData]);

  const [activeChartType, setActiveChartType] = useState<ChartType>("table");

  if (!resultData) {
    // Fallback to text display if data structure is unexpected
    return (
      <div className={styles.inlineToolBlock}>
        <pre className={styles.inlineToolCode}>{textFallback || "No data"}</pre>
      </div>
    );
  }

  const { row_count, generated_sql, spilled, status } = resultData;

  // Reset active tab when data changes
  const effectiveChartType = activeChartType === "table" ? "table" : activeChartType || defaultChartType;

  const tabItems = [
    {
      key: "table",
      label: t("dataSource.chartType.table", "Table"),
      children: <DataTable data={resultData} />,
    },
    {
      key: "bar",
      label: t("dataSource.chartType.bar", "Bar"),
      children: <BarChartView data={resultData} />,
    },
    {
      key: "line",
      label: t("dataSource.chartType.line", "Line"),
      children: <LineChartView data={resultData} />,
    },
    {
      key: "pie",
      label: t("dataSource.chartType.pie", "Pie"),
      children: <PieChartView data={resultData} />,
    },
  ];

  return (
    <div className={styles.dataSourceResult}>
      <div className={styles.dataSourceResultHeader}>
        <Tag color={status === "ok" ? "success" : "default"}>
          {status === "ok" ? t("dataSource.status.ok", "Success") : status}
        </Tag>
        <Text type="secondary">
          {t("dataSource.rowCount", "{count} rows", { count: row_count })}
        </Text>
        {spilled && (
          <Text type="warning">
            {t("dataSource.spilled", "(partial data shown)")}
          </Text>
        )}
      </div>

      <Tabs
        activeKey={effectiveChartType}
        onChange={(key) => setActiveChartType(key as ChartType)}
        items={tabItems}
        size="small"
        className={styles.dataSourceTabs}
      />

      {generated_sql && (
        <details className={styles.dataSourceSqlDetails}>
          <summary>{t("dataSource.generatedSql", "Generated SQL")}</summary>
          <pre className={styles.inlineToolCode}>{generated_sql}</pre>
        </details>
      )}
    </div>
  );
}
