import { DataSourceName } from '@server/types';
import { Manifest } from '@server/mdl/type';
import { IWrenEngineAdaptor } from '../adaptors/wrenEngineAdaptor';
import {
  SupportedDataSource,
  IIbisAdaptor,
  IbisQueryResponse,
  ValidationRules,
  IbisResponse,
} from '../adaptors/ibisAdaptor';
import { getLogger } from '@server/utils';
import { normalizeMssqlSqlForIbis } from '@server/utils/mssqlSqlNormalizer';
import { Project } from '../repositories';
import { PostHogTelemetry, TelemetryEvent } from '../telemetry/telemetry';

const logger = getLogger('QueryService');
logger.level = 'debug';

export const DEFAULT_PREVIEW_LIMIT = 500;

export interface ColumnMetadata {
  name: string;
  type: string;
}

export interface PreviewDataResponse extends IbisResponse {
  columns: ColumnMetadata[];
  data: any[][];
  cacheHit?: boolean;
  cacheCreatedAt?: string;
  cacheOverrodeAt?: string;
  override?: boolean;
}

export interface DescribeStatementResponse {
  columns: ColumnMetadata[];
}

export interface PreviewOptions {
  project: Project;
  modelingOnly?: boolean;
  // if not given, will use the deployed manifest
  manifest: Manifest;
  limit?: number;
  dryRun?: boolean;
  refresh?: boolean;
  cacheEnabled?: boolean;
}

export interface SqlValidateOptions {
  project: Project;
  mdl: Manifest;
  modelingOnly?: boolean;
}

export interface ValidateResponse {
  valid: boolean;
  message?: string;
}

export interface IQueryService {
  preview(
    sql: string,
    options: PreviewOptions,
  ): Promise<IbisResponse | PreviewDataResponse | boolean>;

  describeStatement(
    sql: string,
    options: PreviewOptions,
  ): Promise<DescribeStatementResponse>;

  validate(
    project: Project,
    rule: ValidationRules,
    manifest: Manifest,
    parameters: Record<string, any>,
  ): Promise<ValidateResponse>;
}

const normalizePreviewSqlForIbis = (
  sql: string,
  dataSource: DataSourceName,
  limit?: number,
): { sql: string; limit?: number } => {
  if (dataSource !== DataSourceName.MSSQL) {
    return { sql, limit };
  }

  sql = normalizeMssqlSqlForIbis(sql, dataSource);

  const topMatch = sql.match(/^\s*SELECT\s+(DISTINCT\s+)?TOP\s*\(?\s*(\d+)\s*\)?\s+/i);
  if (!topMatch) {
    return { sql, limit };
  }

  const distinctClause = topMatch[1] || '';
  const topLimit = Number(topMatch[2]);
  const normalizedSql = sql.replace(
    /^\s*SELECT\s+(DISTINCT\s+)?TOP\s*\(?\s*\d+\s*\)?\s+/i,
    `SELECT ${distinctClause}`,
  );

  return {
    sql: normalizedSql,
    limit:
      limit && limit > 0 ? Math.min(limit, topLimit) : topLimit,
  };
};

const SQL_IDENTIFIER_PATTERN =
  String.raw`(?:"[^"]+"|` +
  '`[^`]+`' +
  String.raw`|\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_$]*)`;

const normalizeSqlIdentifier = (identifier: string) => {
  const trimmed = identifier.trim();
  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
    (trimmed.startsWith('`') && trimmed.endsWith('`')) ||
    (trimmed.startsWith('[') && trimmed.endsWith(']'))
  ) {
    return trimmed.slice(1, -1);
  }
  return trimmed;
};

const splitTableReference = (tableReference: string) =>
  tableReference
    .trim()
    .split(/\s*\.\s*/)
    .map(normalizeSqlIdentifier)
    .filter(Boolean);

const extractSqlTableReferences = (sql: string) => {
  const references: string[] = [];
  const tablePattern = new RegExp(
    String.raw`\b(?:FROM|JOIN)\s+(${SQL_IDENTIFIER_PATTERN}(?:\s*\.\s*${SQL_IDENTIFIER_PATTERN})*)`,
    'gi',
  );
  let match: RegExpExecArray | null;
  while ((match = tablePattern.exec(sql))) {
    references.push(splitTableReference(match[1]).join('.'));
  }
  return references;
};

const addTableReferenceName = (
  names: Set<string>,
  parts: Array<string | null | undefined>,
) => {
  const normalizedParts = parts
    .filter((part): part is string => Boolean(part))
    .map((part) => part.toLowerCase());
  if (!normalizedParts.length) {
    return;
  }

  for (let index = 0; index < normalizedParts.length; index += 1) {
    names.add(normalizedParts.slice(index).join('.'));
  }
};

const addTableReferenceColumns = (
  columnsByName: Map<string, Set<string>>,
  parts: Array<string | null | undefined>,
  columns?: Array<{ name?: string }>,
) => {
  const normalizedParts = parts
    .filter((part): part is string => Boolean(part))
    .map((part) => part.toLowerCase());
  const columnNames = new Set(
    (columns || [])
      .map((column) => column.name?.toLowerCase())
      .filter((column): column is string => Boolean(column)),
  );

  if (!normalizedParts.length || !columnNames.size) {
    return;
  }

  for (let index = 0; index < normalizedParts.length; index += 1) {
    columnsByName.set(normalizedParts.slice(index).join('.'), columnNames);
  }
};

const extractCteNames = (sql: string) => {
  const cteNames = new Set<string>();
  const ctePattern = new RegExp(
    String.raw`(?:\bWITH\b|,)\s*(${SQL_IDENTIFIER_PATTERN})\s+AS\s*\(`,
    'gi',
  );
  let match: RegExpExecArray | null;
  while ((match = ctePattern.exec(sql))) {
    cteNames.add(normalizeSqlIdentifier(match[1]).toLowerCase());
  }
  return cteNames;
};

const extractSqlTableAliases = (sql: string) => {
  const aliases = new Map<string, string>();
  const tablePattern = new RegExp(
    String.raw`\b(?:FROM|JOIN)\s+(${SQL_IDENTIFIER_PATTERN}(?:\s*\.\s*${SQL_IDENTIFIER_PATTERN})*)(?:\s+(?:AS\s+)?(${SQL_IDENTIFIER_PATTERN}))?`,
    'gi',
  );
  let match: RegExpExecArray | null;
  while ((match = tablePattern.exec(sql))) {
    const tableReference = splitTableReference(match[1]).join('.').toLowerCase();
    const alias = match[2] ? normalizeSqlIdentifier(match[2]).toLowerCase() : '';

    if (
      alias &&
      ![
        'on',
        'where',
        'join',
        'left',
        'right',
        'inner',
        'outer',
        'full',
        'cross',
      ].includes(alias)
    ) {
      aliases.set(alias, tableReference);
    }
    aliases.set(tableReference, tableReference);

    const lastPart = splitTableReference(match[1]).pop()?.toLowerCase();
    if (lastPart) {
      aliases.set(lastPart, tableReference);
    }
  }
  return aliases;
};

const getManifestQueryableNames = (manifest?: Manifest) => {
  const names = new Set<string>();
  for (const model of manifest?.models || []) {
    if (model.name) names.add(model.name.toLowerCase());
    if (model.tableReference?.table) {
      addTableReferenceName(names, [
        model.tableReference.catalog,
        model.tableReference.schema,
        model.tableReference.table,
      ]);
    }
    if (model.refSql) {
      for (const reference of extractSqlTableReferences(model.refSql)) {
        addTableReferenceName(names, splitTableReference(reference));
      }
    }
  }
  for (const view of manifest?.views || []) {
    if (view.name) names.add(view.name.toLowerCase());
  }
  return names;
};

const getManifestColumnsByQueryableName = (manifest?: Manifest) => {
  const columnsByName = new Map<string, Set<string>>();
  for (const model of manifest?.models || []) {
    if (model.name) {
      addTableReferenceColumns(columnsByName, [model.name], model.columns);
    }
    if (model.tableReference?.table) {
      addTableReferenceColumns(
        columnsByName,
        [
          model.tableReference.catalog,
          model.tableReference.schema,
          model.tableReference.table,
        ],
        model.columns,
      );
    }
  }
  return columnsByName;
};

const validateSqlReferencesManifest = (sql: string, manifest?: Manifest) => {
  const validNames = getManifestQueryableNames(manifest);
  if (!validNames.size) {
    return;
  }

  const cteNames = extractCteNames(sql);
  const invalidReferences = extractSqlTableReferences(sql).filter((reference) => {
    const normalized = reference.toLowerCase();
    const lastPart = splitTableReference(reference).pop()?.toLowerCase();
    return (
      !validNames.has(normalized) &&
      !cteNames.has(normalized) &&
      (!lastPart || !validNames.has(lastPart))
    );
  });

  if (invalidReferences.length) {
    throw new Error(
      `Generated SQL references table(s) not present in the active datasource metadata: ${[
        ...new Set(invalidReferences),
      ].join(', ')}`,
    );
  }

  const columnsByName = getManifestColumnsByQueryableName(manifest);
  if (!columnsByName.size) {
    return;
  }

  const tableAliases = extractSqlTableAliases(sql);
  const invalidColumnReferences = new Set<string>();
  const qualifiedColumnPattern = new RegExp(
    String.raw`(${SQL_IDENTIFIER_PATTERN})\s*\.\s*(${SQL_IDENTIFIER_PATTERN})`,
    'gi',
  );

  let match: RegExpExecArray | null;
  while ((match = qualifiedColumnPattern.exec(sql))) {
    const qualifier = normalizeSqlIdentifier(match[1]).toLowerCase();
    const column = normalizeSqlIdentifier(match[2]).toLowerCase();

    if (cteNames.has(qualifier)) {
      continue;
    }

    const tableName = tableAliases.get(qualifier) || qualifier;
    const validColumns =
      columnsByName.get(tableName) ||
      columnsByName.get(
        splitTableReference(tableName).pop()?.toLowerCase() || '',
      );

    if (validColumns && !validColumns.has(column)) {
      invalidColumnReferences.add(
        `${normalizeSqlIdentifier(match[1])}.${normalizeSqlIdentifier(match[2])}`,
      );
    }
  }

  if (invalidColumnReferences.size) {
    throw new Error(
      `Generated SQL references column(s) not present in the active datasource metadata: ${[
        ...invalidColumnReferences,
      ].join(', ')}`,
    );
  }
};

export const isPreviewDataEmpty = (data?: Partial<PreviewDataResponse> | null) =>
  !data || !Array.isArray(data.data) || data.data.length === 0;

export class QueryService implements IQueryService {
  private readonly ibisAdaptor: IIbisAdaptor;
  private readonly wrenEngineAdaptor: IWrenEngineAdaptor;
  private readonly telemetry: PostHogTelemetry;

  constructor({
    ibisAdaptor,
    wrenEngineAdaptor,
    telemetry,
  }: {
    ibisAdaptor: IIbisAdaptor;
    wrenEngineAdaptor: IWrenEngineAdaptor;
    telemetry: PostHogTelemetry;
  }) {
    this.ibisAdaptor = ibisAdaptor;
    this.wrenEngineAdaptor = wrenEngineAdaptor;
    this.telemetry = telemetry;
  }

  public async preview(
    sql: string,
    options: PreviewOptions,
  ): Promise<IbisResponse | PreviewDataResponse | boolean> {
    const {
      project,
      manifest: mdl,
      limit,
      dryRun,
      refresh,
      cacheEnabled,
    } = options;
    const { type: dataSource, connectionInfo } = project;
    validateSqlReferencesManifest(sql, mdl);
    if (this.useEngine(dataSource)) {
      if (dryRun) {
        logger.debug('Using wren engine to dry run');
        await this.wrenEngineAdaptor.dryRun(sql, {
          manifest: mdl,
          limit,
        });
        return true;
      } else {
        logger.debug('Using wren engine to preview');
        const data = await this.wrenEngineAdaptor.previewData(sql, mdl, limit);
        return data as PreviewDataResponse;
      }
    } else {
      this.checkDataSourceIsSupported(dataSource);
      logger.debug('Use ibis adaptor to preview');
      if (dryRun) {
        return await this.ibisDryRun(sql, dataSource, connectionInfo, mdl);
      } else {
        return await this.ibisQuery(
          sql,
          dataSource,
          connectionInfo,
          mdl,
          limit,
          refresh,
          cacheEnabled,
        );
      }
    }
  }

  public async describeStatement(
    sql: string,
    options: PreviewOptions,
  ): Promise<DescribeStatementResponse> {
    try {
      // preview data with limit 1 to get column metadata
      options.limit = 1;
      const res = (await this.preview(sql, options)) as PreviewDataResponse;
      return { columns: res.columns };
    } catch (err: any) {
      logger.debug(`Got error when describing statement: ${err.message}`);
      throw err;
    }
  }

  public async validate(
    project,
    rule: ValidationRules,
    manifest: Manifest,
    parameters: Record<string, any>,
  ): Promise<ValidateResponse> {
    const { type: dataSource, connectionInfo } = project;
    const res = await this.ibisAdaptor.validate(
      dataSource,
      rule,
      connectionInfo,
      manifest,
      parameters,
    );
    return res;
  }

  private useEngine(dataSource: DataSourceName): boolean {
    if (dataSource === DataSourceName.DUCKDB) {
      return true;
    } else {
      return false;
    }
  }

  private checkDataSourceIsSupported(dataSource: DataSourceName) {
    if (
      !Object.prototype.hasOwnProperty.call(SupportedDataSource, dataSource)
    ) {
      throw new Error(`Unsupported datasource for ibis: "${dataSource}"`);
    }
  }

  private async ibisDryRun(
    sql: string,
    dataSource: DataSourceName,
    connectionInfo: any,
    mdl: Manifest,
  ): Promise<IbisResponse> {
    const normalizedQuery = normalizePreviewSqlForIbis(sql, dataSource).sql;
    const event = TelemetryEvent.IBIS_DRY_RUN;
    try {
      const res = await this.ibisAdaptor.dryRun(normalizedQuery, {
        dataSource,
        connectionInfo,
        mdl,
      });
      this.sendIbisEvent(event, res, { dataSource, sql: normalizedQuery });
      return {
        correlationId: res.correlationId,
      };
    } catch (err: any) {
      this.sendIbisFailedEvent(event, err, {
        dataSource,
        sql: normalizedQuery,
      });
      throw err;
    }
  }

  private async ibisQuery(
    sql: string,
    dataSource: DataSourceName,
    connectionInfo: any,
    mdl: Manifest,
    limit: number,
    refresh?: boolean,
    cacheEnabled?: boolean,
  ): Promise<PreviewDataResponse> {
    const normalizedPreview = normalizePreviewSqlForIbis(sql, dataSource, limit);
    const event = TelemetryEvent.IBIS_QUERY;
    try {
      const res = await this.ibisAdaptor.query(normalizedPreview.sql, {
        dataSource,
        connectionInfo,
        mdl,
        limit: normalizedPreview.limit,
        refresh,
        cacheEnabled,
      });
      this.sendIbisEvent(event, res, {
        dataSource,
        sql: normalizedPreview.sql,
      });
      const data = this.transformDataType(res);
      return {
        correlationId: res.correlationId,
        cacheHit: res.cacheHit,
        cacheCreatedAt: res.cacheCreatedAt,
        cacheOverrodeAt: res.cacheOverrodeAt,
        override: res.override,
        ...data,
      };
    } catch (err: any) {
      this.sendIbisFailedEvent(event, err, {
        dataSource,
        sql: normalizedPreview.sql,
      });
      throw err;
    }
  }

  private transformDataType(data: IbisQueryResponse): PreviewDataResponse {
    const columns = data.columns;
    const dtypes = data.dtypes;
    const transformedColumns = columns.map((column) => {
      let type = 'unknown';
      if (dtypes && dtypes[column]) {
        type = dtypes[column] === 'object' ? 'string' : dtypes[column];
      }
      if (type === 'unknown') {
        logger.debug(`Did not find type mapping for "${column}"`);
        logger.debug(
          `dtypes mapping: ${dtypes ? JSON.stringify(dtypes, null, 2) : 'undefined'} `,
        );
      }
      return {
        name: column,
        type,
      } as ColumnMetadata;
    });
    return {
      columns: transformedColumns,
      data: data.data,
    } as PreviewDataResponse;
  }

  private sendIbisEvent(
    event: TelemetryEvent,
    res: IbisResponse,
    others: Record<string, any>,
  ) {
    this.telemetry.sendEvent(event, {
      correlationId: res.correlationId,
      processTime: res.processTime,
      ...others,
    });
  }

  private sendIbisFailedEvent(
    event: TelemetryEvent,
    err: any,
    others: Record<string, any>,
  ) {
    this.telemetry.sendEvent(
      event,
      {
        correlationId: err.extensions?.other?.correlationId,
        processTime: err.extensions?.other?.processTime,
        error: err.message,
        ...others,
      },
      err.extensions?.service,
      false,
    );
  }
}
