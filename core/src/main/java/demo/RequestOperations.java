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
    private final String diagnosticCode;
    public DataAccessUnavailable(Throwable cause) { this(cause,null); }
    public DataAccessUnavailable(Throwable cause,String code) {
      super("data access unavailable",cause);
      this.diagnosticCode=(code!=null&&code.matches("[A-Z0-9]{5}"))?code:"UNCLASSIFIED";
    }
    /** Non-secret classification only; no database library type crosses this interface. */
    public String diagnosticCode(){return diagnosticCode;}
  }
  Admission create(String externalId,String customerId,String productCode) throws DataAccessUnavailable;
  Detail find(String id) throws DataAccessUnavailable;
}
