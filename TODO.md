# OpenRed TODO List

## Periodic radiation emails (monthly report day 2, personal digest day 16)

Live since 2026-10-16 (see `measures/email_runs.py`, queue `openred-emails`, command `scheduled_emails`).

- [ ] **2026-11-02: check the first real monthly report** (October data, to every contributor)
  - Run `manage.py scheduled_emails status`: expect `monthly_report 2026-10` as `sent`, 0 failures
  - Check `/var/log/supervisor/openred-emails.log` if it is not
- [ ] **2026-11-16: check the first automatic personal digest** (`activity_digest 2026-11`)
- [ ] **GDPR for the periodic emails**: unsubscribe link / consent, honour opt-outs before the next mailing
- [ ] **Fix under-estimated kilometres** (500 m per-segment cap in `parse_json`/`parse_csv`); proposed fix: 150 km/h speed cap + recompute tracks. The emails show km, so they are affected
- [ ] Optional: tailor the "we miss you" email (suggest an unmeasured zone near where the person measured before)

## H3 Aggregation Enhancements

### High Priority

- [ ] **Configure Redis for query caching**
  - Currently using `LocMemCache` (local memory)
  - Switch to Redis backend for production scalability
  - Cache H3 aggregation results (TTL: 1 hour recommended)
  - Example cache key: `h3_rad_{resolution}_{project}_{campaign}_{track}_{min_count}`
  - Benefits: Reduce PostgreSQL load, faster response times for repeated queries

- [ ] **Performance testing with production data**
  - Test H3 aggregation with realistic dataset sizes
  - Measure query response times at different resolutions
  - Compare PostgreSQL native vs Python approach (if historical data available)
  - Establish performance baselines and SLAs

- [ ] **Document H3 aggregation endpoint in MkDocs**
  - Create detailed guide in `docs/api-reference/h3-aggregation.md`
  - Include:
    - Endpoint description and use cases
    - Query parameters documentation
    - Response format examples
    - Resolution level guide (0-15 with real-world examples)
    - Performance recommendations
    - Code examples (Python, JavaScript, curl)
  - Currently only documented in Swagger/ReDoc

### Medium Priority

- [ ] **Add cache invalidation strategy**
  - Invalidate cache when new measurements are added
  - Use Django signals on `RadiationMeasurement.post_save`
  - Consider selective invalidation (only affected project/campaign)
  - Implement cache warming for frequently accessed queries

- [ ] **Optimize boundary data transfer**
  - Current implementation sends full polygon boundaries
  - Consider GeoJSON simplification for large datasets
  - Evaluate binary formats (WKB) for reduced payload size
  - Add optional `include_boundaries` parameter

- [ ] **Add monitoring and alerting**
  - Track H3 aggregation query performance
  - Monitor cache hit/miss ratios
  - Alert on slow queries (> 2 seconds)
  - Log PostgreSQL query execution plans for optimization

- [ ] **Implement rate limiting**
  - Add rate limiting to H3 aggregation endpoints
  - Protect against abuse of computationally expensive queries
  - Suggested limits: 60 requests/minute per IP for unauthenticated users

### Low Priority

- [ ] **Add more aggregation functions**
  - Median values (using PostgreSQL `PERCENTILE_CONT`)
  - 95th/99th percentiles for outlier detection
  - Variance and coefficient of variation
  - Temporal aggregations (daily/weekly averages)

- [ ] **Create visualization examples**
  - Add example frontend code for rendering H3 hexagons
  - Integration examples with Leaflet/MapLibre
  - Color scales and legends for measurements
  - Interactive resolution switcher

- [ ] **Add batch export functionality**
  - Export H3 aggregated data to GeoJSON
  - Support CSV export with WKT geometry
  - Add KML export for Google Earth
  - Implement streaming for large datasets

- [ ] **Improve test coverage**
  - Add performance regression tests
  - Test with very large datasets (>1M measurements)
  - Test edge cases (polar coordinates, antimeridian crossing)
  - Add load testing scenarios

## Infrastructure

- [ ] **Production PostgreSQL optimization**
  - Verify H3 extension version (use latest stable)
  - Configure PostgreSQL work_mem for spatial queries
  - Add query result caching at PostgreSQL level
  - Monitor and optimize spatial indexes

- [ ] **Redis configuration for production**
  - Update `settings.py` to use Redis cache backend
  - Configure cache TTL policies
  - Set up Redis persistence (AOF or RDB)
  - Monitor Redis memory usage

## Documentation

- [ ] **Update API changelog**
  - Document H3 aggregation feature release
  - Note breaking changes (if any)
  - Include migration guide from Python to PostgreSQL approach

- [ ] **Create performance documentation**
  - Document expected query times by dataset size
  - Resolution selection guidelines
  - Caching best practices
  - Troubleshooting guide

---

## Completed ✅

- [x] Implement PostgreSQL-native H3 aggregation (2024-11-04)
- [x] Replace Python iteration with SQL queries
- [x] Add GeoDjango support with PostGIS
- [x] Create location fields with spatial indexes
- [x] Migrate existing data to populate location fields
- [x] Make H3 endpoints public (no auth required for GET)
- [x] Fix table names in SQL queries
- [x] Fix PostGIS geometry extraction (ST_X, ST_Y)
- [x] Implement boundary extraction with ST_DumpPoints
- [x] Create comprehensive test suite (10 tests, all passing)
- [x] Handle dateTime as string in model save() method
- [x] Add Device/DeviceModel support in tests

---

## Notes

- All H3 aggregation queries now run entirely in PostgreSQL using h3-pg extension
- Performance gain: ~10x faster for datasets > 10k measurements
- Spatial indexes (GiST) are being utilized effectively
- Tests passing: 10/10 ✅
