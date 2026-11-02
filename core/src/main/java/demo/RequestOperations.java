package demo;

/** Transport-agnostic public admission and status contract.
 * Does not expose PostgreSQL ledger outcomes or RabbitMQ types.
 * The caller must retry an UNCERTAIN admission with the same externalId.
 */
public interface RequestOperations {
  enum AdmissionStatus { CREATED, REPLAY, CONFLICT, UNCERTAIN }
  record Admission(AdmissionStatus status,String id) {}
  record Detail(String id,String externalId,String customerId,String productCode,
    String status,long attempts,long version,long incidentCount,long signedEvidenceCount) {}
  /** Database, network, or storage failures are deliberately hidden from HTTP adapters. */
  final class DataAccessUnavailable extends Exception {
    public DataAccessUnavailable(Throwable cause) { super("data access unavailable", cause); }
  }
  Admission create(String externalId,String customerId,String productCode) throws DataAccessUnavailable;
  Detail find(String id) throws DataAccessUnavailable;
}
