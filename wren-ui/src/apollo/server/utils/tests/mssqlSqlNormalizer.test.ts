import { DataSourceName } from '../../types';
import { normalizeMssqlSqlForIbis } from '../mssqlSqlNormalizer';

describe('mssqlSqlNormalizer', () => {
  it('rewrites CWSales OTD date aliases before MSSQL preview execution', () => {
    const normalized = normalizeMssqlSqlForIbis(
      `
      SELECT
        DATEPART(YEAR, "dbo_tblSalesHistory"."OTD_Date") AS "year",
        DATEPART(MONTH, "dbo_tblSalesHistory"."OTD_Date") AS "month",
        "dbo_tblSalesHistory"."MarketType" AS "MarketType",
        SUM("dbo_tblSalesHistory"."Qty") AS "TotalQty"
      FROM "dbo_tblSalesHistory"
      GROUP BY
        DATEPART(YEAR, "dbo_tblSalesHistory"."OTD_Date"),
        DATEPART(MONTH, "dbo_tblSalesHistory"."OTD_Date"),
        "dbo_tblSalesHistory"."MarketType"
      ORDER BY
        DATEPART(YEAR, "dbo_tblSalesHistory"."OTD_Date"),
        DATEPART(MONTH, "dbo_tblSalesHistory"."OTD_Date")
      `,
      DataSourceName.MSSQL,
    );

    expect(normalized).not.toContain('OTD_Date');
    expect(normalized).toContain('"dbo_tblSalesHistory"."InvDate"');
  });

  it('rewrites CWSales FixLogId aliases before MSSQL preview execution', () => {
    const normalized = normalizeMssqlSqlForIbis(
      `
      SELECT
        "SalesPerson",
        Country,
        COUNT("dbo_qSales1"."FixLogId") AS NumberOfInvoices
      FROM "dbo_qSales1"
      GROUP BY "SalesPerson", Country
      ORDER BY NumberOfInvoices DESC
      LIMIT 1
      `,
      DataSourceName.MSSQL,
    );

    expect(normalized).not.toContain('FixLogId');
    expect(normalized).toContain('"dbo_qSales1"."InvoiceNo"');
  });

  it('does not rewrite CWSales aliases for non-MSSQL datasources', () => {
    const sql = 'SELECT "dbo_tblSalesHistory"."OTD_Date" FROM "dbo_tblSalesHistory"';

    expect(normalizeMssqlSqlForIbis(sql, DataSourceName.POSTGRES)).toBe(sql);
  });

  it('rewrites knowledge article time buckets', () => {
    const normalized = normalizeMssqlSqlForIbis(
      `
      SELECT "YEAR", COUNT("dbo_knowledge_articles"."id") AS "article_count"
      FROM "dbo_knowledge_articles"
      GROUP BY "YEAR"
      ORDER BY "YEAR" ASC
      `,
      DataSourceName.MSSQL,
    );

    expect(normalized).not.toContain('SELECT "YEAR"');
    expect(normalized).not.toContain('GROUP BY "YEAR"');
    expect(normalized).toContain(
      'DATEPART(YEAR, "dbo_knowledge_articles"."created_at") AS "year"',
    );
  });

  it('rewrites stale last_update_date references for debug entry trends', () => {
    const normalized = normalizeMssqlSqlForIbis(
      `
      SELECT
        DATEPART(YEAR, last_update_date) AS "year",
        DATEPART(MONTH, last_update_date) AS "month",
        "dbo_DebugEntries"."BusinessUnit" AS "manufacturing_unit",
        COUNT("dbo_DebugEntries"."DebugEntryId") AS "throughput"
      FROM "dbo_DebugEntries"
      GROUP BY
        DATEPART(YEAR, last_update_date),
        DATEPART(MONTH, last_update_date),
        "dbo_DebugEntries"."BusinessUnit"
      ORDER BY
        DATEPART(YEAR, last_update_date),
        DATEPART(MONTH, last_update_date)
      `,
      DataSourceName.MSSQL,
    );

    expect(normalized).not.toContain('last_update_date');
    expect(normalized).toContain('DATEPART(YEAR, "dbo_DebugEntries"."DateIn")');
    expect(normalized).toContain('DATEPART(MONTH, "dbo_DebugEntries"."DateIn")');
  });

  it('rewrites hallucinated knowledge article fields', () => {
    const normalized = normalizeMssqlSqlForIbis(
      `
      SELECT
        AVG("dbo_knowledge_articles"."effectiveness_score") AS "avg_effectiveness",
        "dbo_knowledge_articles"."created_by" AS "created_by"
      FROM "dbo_knowledge_articles"
      GROUP BY "dbo_knowledge_articles"."created_by"
      `,
      DataSourceName.MSSQL,
    );

    expect(normalized).not.toContain('effectiveness_score');
    expect(normalized).not.toContain('"dbo_knowledge_articles"."created_by"');
    expect(normalized).toContain(
      'AVG("dbo_knowledge_articles"."helpful") AS "avg_effectiveness"',
    );
    expect(normalized).toContain('"dbo_knowledge_articles"."author" AS "author"');
    expect(normalized).toContain('GROUP BY "dbo_knowledge_articles"."author"');
  });

  it('rewrites hallucinated kb article creator fields', () => {
    const normalized = normalizeMssqlSqlForIbis(
      `
      SELECT created_by, COUNT(*) AS article_count
      FROM dbo_kb_articles
      GROUP BY created_by
      ORDER BY article_count DESC
      `,
      DataSourceName.MSSQL,
    );

    expect(normalized).not.toContain('created_by,');
    expect(normalized).not.toContain('GROUP BY created_by');
    expect(normalized).toContain('"created_by_user_id"');
  });
});
