import { TelemetryEvent } from '../../telemetry/telemetry';
import { DataSourceName } from '../../types';
import { QueryService } from '../queryService';

describe('QueryService', () => {
  let mockIbisAdaptor;
  let mockWrenEngineAdaptor;
  let mockTelemetry;
  let queryService;

  beforeEach(() => {
    mockIbisAdaptor = {
      query: jest.fn(),
      dryRun: jest.fn(),
    };
    mockWrenEngineAdaptor = {};
    mockTelemetry = new MockTelemetry();

    queryService = new QueryService({
      ibisAdaptor: mockIbisAdaptor,
      wrenEngineAdaptor: mockWrenEngineAdaptor,
      telemetry: mockTelemetry,
    });
  });

  afterEach(() => {
    mockTelemetry.records = [];
    jest.clearAllMocks();
  });

  it('should return true and send event when previewing via ibis dry run succeeds', async () => {
    mockIbisAdaptor.dryRun.mockResolvedValue({
      correlationId: '123',
      processTime: '1s',
    });

    const res = await queryService.preview('SELECT * FROM test', {
      project: { type: DataSourceName.POSTGRES, connectionInfo: {} },
      manifest: {},
      dryRun: true,
    });

    expect(res).toEqual({ correlationId: '123' });
    expect(mockTelemetry.records).toHaveLength(1);
    expect(mockTelemetry.records[0]).toEqual({
      event: TelemetryEvent.IBIS_DRY_RUN,
      properties: {
        correlationId: '123',
        processTime: '1s',
        sql: 'SELECT * FROM test',
        dataSource: DataSourceName.POSTGRES,
      },
      actionSuccess: true,
    });
  });

  it('should send event when previewing via ibis dry run fails', async () => {
    mockIbisAdaptor.dryRun.mockRejectedValue({
      message: 'Error message',
      extensions: {
        other: {
          correlationId: '123',
          processTime: '1s',
        },
      },
    });

    try {
      await queryService.preview('SELECT * FROM test', {
        project: { type: DataSourceName.POSTGRES, connectionInfo: {} },
        manifest: {},
        dryRun: true,
      });
    } catch (e) {
      expect(e.message).toEqual('Error message');
      expect(e.extensions.other.correlationId).toEqual('123');
      expect(e.extensions.other.processTime).toEqual('1s');
    }

    expect(mockTelemetry.records).toHaveLength(1);
    expect(mockTelemetry.records[0]).toEqual({
      event: TelemetryEvent.IBIS_DRY_RUN,
      properties: {
        correlationId: '123',
        processTime: '1s',
        sql: 'SELECT * FROM test',
        dataSource: DataSourceName.POSTGRES,
        error: 'Error message',
      },
      actionSuccess: false,
      service: undefined,
    });
  });

  it('should return data and send event when previewing via ibis query succeeds', async () => {
    mockIbisAdaptor.query.mockResolvedValue({
      data: [],
      columns: [],
      dtypes: [],
      correlationId: '123',
      processTime: '1s',
    });

    const res = await queryService.preview('SELECT * FROM test', {
      project: { type: DataSourceName.POSTGRES, connectionInfo: {} },
      manifest: {},
      limit: 10,
    });

    expect(res.data).toEqual([]);
    expect(mockTelemetry.records).toHaveLength(1);
    expect(mockTelemetry.records[0]).toEqual({
      event: TelemetryEvent.IBIS_QUERY,
      properties: {
        correlationId: '123',
        processTime: '1s',
        sql: 'SELECT * FROM test',
        dataSource: DataSourceName.POSTGRES,
      },
      actionSuccess: true,
    });
  });

  it('should send event when previewing via ibis query fails', async () => {
    mockIbisAdaptor.query.mockRejectedValue({
      message: 'Error message',
      extensions: {
        other: {
          correlationId: '123',
          processTime: '1s',
        },
      },
    });

    await expect(
      queryService.preview('SELECT * FROM test', {
        project: { type: DataSourceName.POSTGRES, connectionInfo: {} },
        manifest: {},
      }),
    ).rejects.toMatchObject({
      message: 'Error message',
      extensions: {
        other: {
          correlationId: '123',
          processTime: '1s',
        },
      },
    });

    expect(mockTelemetry.records).toHaveLength(1);
    expect(mockTelemetry.records[0]).toEqual({
      event: TelemetryEvent.IBIS_QUERY,
      properties: {
        correlationId: '123',
        processTime: '1s',
        sql: 'SELECT * FROM test',
        dataSource: DataSourceName.POSTGRES,
        error: 'Error message',
      },
      actionSuccess: false,
      service: undefined,
    });
  });

  it('should reject sql that references tables outside the active manifest before ibis dry run', async () => {
    await expect(
      queryService.preview('SELECT * FROM dbo_failure_patterns', {
        project: { type: DataSourceName.POSTGRES, connectionInfo: {} },
        manifest: {
          models: [
            {
              name: 'dbo_tblSales',
              tableReference: { table: 'dbo_tblSales' },
            },
          ],
        },
        dryRun: true,
      }),
    ).rejects.toThrow(
      'Generated SQL references table(s) not present in the active datasource metadata: dbo_failure_patterns',
    );

    expect(mockIbisAdaptor.dryRun).not.toHaveBeenCalled();
  });

  it('should allow active manifest table references before ibis dry run', async () => {
    mockIbisAdaptor.dryRun.mockResolvedValue({
      correlationId: '123',
      processTime: '1s',
    });

    await queryService.preview('SELECT * FROM wrenai.public.dbo_tblSales', {
      project: { type: DataSourceName.POSTGRES, connectionInfo: {} },
      manifest: {
        models: [
          {
            name: 'dbo_tblSales',
            tableReference: { table: 'dbo_tblSales' },
          },
        ],
      },
      dryRun: true,
    });

    expect(mockIbisAdaptor.dryRun).toHaveBeenCalledTimes(1);
  });

  it('should rewrite physical table references to active model names before ibis dry run', async () => {
    mockIbisAdaptor.dryRun.mockResolvedValue({
      correlationId: '123',
      processTime: '1s',
    });

    await queryService.preview(
      'SELECT "wrenai.public.dbo_failure"."created_at" FROM "wrenai.public.dbo_failure"',
      {
        project: { type: DataSourceName.POSTGRES, connectionInfo: {} },
        manifest: {
          models: [
            {
              name: 'dbo_failure_patterns',
              tableReference: {
                catalog: 'wrenai',
                schema: 'public',
                table: 'dbo_failure',
              },
              columns: [{ name: 'created_at' }],
            },
          ],
        },
        dryRun: true,
      },
    );

    expect(mockIbisAdaptor.dryRun).toHaveBeenCalledWith(
      'SELECT "dbo_failure_patterns"."created_at" FROM "dbo_failure_patterns"',
      expect.any(Object),
    );
  });

  it('should rewrite multipart physical table references to active model names before ibis dry run', async () => {
    mockIbisAdaptor.dryRun.mockResolvedValue({
      correlationId: '123',
      processTime: '1s',
    });

    await queryService.preview(
      'SELECT "wrenai"."public"."dbo_failure"."created_at" FROM "wrenai"."public"."dbo_failure"',
      {
        project: { type: DataSourceName.POSTGRES, connectionInfo: {} },
        manifest: {
          models: [
            {
              name: 'dbo_failure_patterns',
              tableReference: {
                catalog: 'wrenai',
                schema: 'public',
                table: 'dbo_failure',
              },
              columns: [{ name: 'created_at' }],
            },
          ],
        },
        dryRun: true,
      },
    );

    expect(mockIbisAdaptor.dryRun).toHaveBeenCalledWith(
      'SELECT "dbo_failure_patterns"."created_at" FROM "dbo_failure_patterns"',
      expect.any(Object),
    );
  });

  it('should allow source tables referenced by active manifest refSql before ibis dry run', async () => {
    mockIbisAdaptor.dryRun.mockResolvedValue({
      correlationId: '123',
      processTime: '1s',
    });

    await queryService.preview('SELECT * FROM dbo_repair_logs', {
      project: { type: DataSourceName.POSTGRES, connectionInfo: {} },
      manifest: {
        models: [
          {
            name: 'repair_logs',
            refSql: 'SELECT created_at, ticket_id FROM dbo_repair_logs',
          },
        ],
      },
      dryRun: true,
    });

    expect(mockIbisAdaptor.dryRun).toHaveBeenCalledTimes(1);
  });
});

class MockTelemetry {
  records: any[] = [];
  sendEvent(
    event: TelemetryEvent,
    properties: Record<string, any> = {},
    service: any,
    actionSuccess: boolean = true,
  ) {
    this.records.push({ event, properties, service, actionSuccess });
  }
}
