package demo;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.sql.*;
import java.util.*;

/**
 * The REST admission boundary for the integrated application.
 * Identity, payload, initial state and outbox enter PostgreSQL in ONE transaction.
 * The RabbitMQ message is only a notification; PostgreSQL remains authoritative.
 */
public final class PublicRequests implements RequestOperations {
  private final JdbcRabbitLedger ledger;
  private final P15Policy policy;
  private final long retentionMs;
  public PublicRequests(JdbcRabbitLedger ledger,P15Policy policy,long retentionMs){
    this.ledger=ledger;this.policy=policy;this.retentionMs=retentionMs;
  }
  static String fingerprint(String customerId,String productCode) {
    try{
      MessageDigest digest=MessageDigest.getInstance("SHA-256");
      byte[] raw=(customerId+"\u0000"+productCode).getBytes(StandardCharsets.UTF_8);
      return "public:"+HexFormat.of().formatHex(digest.digest(raw));
    }catch(Exception e){throw new IllegalStateException(e);}
  }
  @Override public Admission create(String externalId,String customerId,String productCode)throws DataAccessUnavailable {
    try { return createInDatabase(externalId, customerId, productCode); }
    catch(SQLException e){ throw new DataAccessUnavailable(e); }
  }
  private Admission createInDatabase(String externalId,String customerId,String productCode)throws SQLException{
    String hash=fingerprint(customerId,productCode);
    ledger.registerPolicy(policy);
    // INSERT ... ON CONFLICT DO NOTHING waits for competing transactions;
    // the subsequent SELECT sees the winner at READ COMMITTED.
    try(Connection c=ledger.connect()) {
      JdbcRabbitLedger.begin(c);
      try {
        long now=ledger.authoritativeNow(c);
        String id=UUID.randomUUID().toString();
        int inserted=JdbcRabbitLedger.exec(c,
          "INSERT INTO p9_requests (rid,idem_key,fingerprint,stage,ver,attempts,due_ms,lease_ms,audit_ms,policy_revision,retention_ms,customer_id,product_code) " +
          "VALUES (?,?,?,'READY',0,0,?,0,?,?,?,?,?) ON CONFLICT DO NOTHING",
          id,externalId,hash,now,now+1000,policy.revision,retentionMs,customerId,productCode);
        if(inserted==1){
          JdbcRabbitLedger.exec(c,"INSERT INTO p9_outbox(nid,rid,due_ms,token_until) VALUES (?,?,?,0)","initial:"+id,id,now);
          JdbcRabbitLedger.Outcome result=JdbcRabbitLedger.commit(c);
          return new Admission(result==JdbcRabbitLedger.Outcome.COMMITTED
            ? AdmissionStatus.CREATED : AdmissionStatus.UNCERTAIN,
            result==JdbcRabbitLedger.Outcome.COMMITTED?id:null);
        }
        String existingId=null,existingHash=null,oldCustomer=null,oldProduct=null;
        try(PreparedStatement p=c.prepareStatement("SELECT rid,fingerprint,customer_id,product_code FROM p9_requests WHERE idem_key=?")){
          p.setString(1,externalId);
          try(ResultSet r=p.executeQuery()) {if(r.next()){
            existingId=r.getString(1);existingHash=r.getString(2);oldCustomer=r.getString(3);oldProduct=r.getString(4);
          }}
        }
        c.rollback();
        if(existingId!=null && hash.equals(existingHash) && customerId.equals(oldCustomer) && productCode.equals(oldProduct))
          return new Admission(AdmissionStatus.REPLAY,existingId);
        return new Admission(AdmissionStatus.CONFLICT,null);
      }catch(SQLException e){JdbcRabbitLedger.rollback(c);throw e;}
    }
  }
  @Override public Detail find(String id)throws DataAccessUnavailable {
    try { return findInDatabase(id); }
    catch(SQLException e){ throw new DataAccessUnavailable(e); }
  }
  private Detail findInDatabase(String id)throws SQLException{
    try(Connection c=ledger.connect();PreparedStatement p=c.prepareStatement(
      "SELECT r.rid,r.idem_key,r.customer_id,r.product_code,r.stage,r.attempts,r.ver," +
      "(SELECT count(*) FROM p14_evidence_incident i WHERE i.rid=r.rid),"+
      "(SELECT count(*) FROM p20_signed_attestation a JOIN p9_dispatch d ON d.did=a.did WHERE d.rid=r.rid) " +
      "FROM p9_requests r WHERE r.rid=? AND r.customer_id IS NOT NULL")){
      p.setString(1,id);
      try(ResultSet r=p.executeQuery()) {
        return r.next()?new Detail(r.getString(1),r.getString(2),r.getString(3),r.getString(4),
          r.getString(5),r.getLong(6),r.getLong(7),r.getLong(8),r.getLong(9)):null;
      }
    }
  }
}
