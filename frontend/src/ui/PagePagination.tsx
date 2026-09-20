import { Pagination } from "antd";

export function PagePagination({
  offset,
  pageSize,
  total,
  onChange,
  loading = false,
  unit = "行",
}: {
  offset: number;
  pageSize: number;
  total: number;
  onChange: (offset: number) => void;
  loading?: boolean;
  unit?: string;
}) {
  return (
    <div className="ui-pagination">
      <span>
        {total
          ? `${offset + 1}–${Math.min(offset + pageSize, total)} / ${total.toLocaleString()} ${unit}`
          : `0 ${unit}`}
      </span>
      <Pagination
        size="small"
        simple={{ readOnly: true }}
        current={Math.floor(offset / pageSize) + 1}
        total={total}
        pageSize={pageSize}
        disabled={loading}
        showSizeChanger={false}
        onChange={(page) => onChange((page - 1) * pageSize)}
        itemRender={(_, type, element) =>
          type === "prev" || type === "next" ? (
            <button
              type="button"
              className="ui-page-step"
              aria-label={type === "prev" ? "上一页" : "下一页"}
              disabled={
                loading ||
                (type === "prev" ? offset === 0 : offset + pageSize >= total)
              }
            >
              {type === "prev" ? "上一页" : "下一页"}
            </button>
          ) : (
            element
          )
        }
      />
    </div>
  );
}
