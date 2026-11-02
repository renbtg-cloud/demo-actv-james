package demo;

import java.sql.*;
import java.util.*;

/**
 * P12 reused State Store JDBC slice from P11, now exercised with real RabbitMQ AMQP.
 * Java 21, portable JDBC subset; tested using HSQLDB 2.7.4 and PostgreSQL 17.11 SERVER MODE.
 * All times are injected for reproducible experiments: not a production lease clock.
 * Confirmed COMMIT is the only successful transition outcome.
 */
public final class JdbcRabbitLedger {
  public enum Outcome { COMMITTED, ALREADY, STALE, CONFLICT, COMMIT_UNKNOWN }
  final String url, user, pass;
  public JdbcRabbitLedger(String url,String user,String pass) {this.url=url;this.user=user;this.pass=pass;}
  Connection connect() throws SQLException {return DriverManager.getConnection(url,user,pass);}
  static void begin(Connection c) throws SQLException {c.setTransactionIsolation(Connection.TRANSACTION_READ_COMMITTED);c.setAutoCommit(false);}
  static Outcome commit(Connection c) {try {c.commit();return Outcome.COMMITTED;}catch(SQLException ex){return Outcome.COMMIT_UNKNOWN;}}
  static int exec(Connection c,String sql,Object... args) throws SQLException {
    try(PreparedStatement p=c.prepareStatement(sql)) {bind(p,args);return p.executeUpdate();}
  }
  static void bind(PreparedStatement p,Object... args)throws SQLException {for(int i=0;i<args.length;i++)p.setObject(i+1,args[i]);}
  static String query(Connection c,String sql,Object... args)throws SQLException {
    try(PreparedStatement p=c.prepareStatement(sql)){bind(p,args);try(ResultSet rs=p.executeQuery()){return rs.next()?rs.getString(1):null;}}
  }
  static long queryLong(Connection c,String sql,Object... args)throws SQLException {
    try(PreparedStatement p=c.prepareStatement(sql)){bind(p,args);try(ResultSet rs=p.executeQuery()){return rs.next()?rs.getLong(1):-1L;}}
  }
  static void rollback(Connection c){try{c.rollback();}catch(SQLException ignored){}}
  /** P14: all lease and due decisions use time read on the authoritative DB connection,
   * after taking the serializing row lock. No JVM wall clock controls a fenced decision. */
  long authoritativeNow(Connection c) throws SQLException {
    if (!url.startsWith("jdbc:postgresql:")) return System.currentTimeMillis(); // P14 PG integration only; HSQLDB port pending.
    return queryLong(c, "SELECT (EXTRACT(EPOCH FROM clock_timestamp())*1000)::BIGINT");
  }
  void lockRequest(Connection c, String rid) throws SQLException {
    try (PreparedStatement p=c.prepareStatement("SELECT rid FROM p9_requests WHERE rid=? FOR UPDATE")) {
      p.setString(1,rid); try(ResultSet ignored=p.executeQuery()){ if(ignored.next()) {/* held until TX end */} }
    }
  }
  void lockOutbox(Connection c, String nid) throws SQLException {
    try (PreparedStatement p=c.prepareStatement("SELECT nid FROM p9_outbox WHERE nid=? FOR UPDATE")) {
      p.setString(1,nid); try(ResultSet ignored=p.executeQuery()){ if(ignored.next()) {/* held */} }
    }
  }
  /** Stored decisive mock receipts are checked only after acquiring the same request lock
   * used by observation insertion. This is not a general provider receipt verifier. */
  String decisiveProof(Connection c, String rid, String key) throws SQLException {
    boolean sawA=false,sawR=false;
    try (PreparedStatement p=c.prepareStatement("SELECT o.action FROM p9_observation o JOIN p9_dispatch d ON d.did=o.did WHERE d.rid=?")) {
      p.setString(1,rid);try(ResultSet rs=p.executeQuery()){while(rs.next()){
        String a=rs.getString(1);
        if("ADOPT_ACTIVATED".equals(a)||"CONTRACT_SUCCESS".equals(a))sawA=true;
        else if("ADOPT_REJECTED".equals(a)||"CONTRACT_REJECTION".equals(a))sawR=true;
      }}
    }
    return sawA&&sawR?"CONFLICT":sawA?"DONE":sawR?"REJECTED":null;
  }
  /** Reconcile old/late durable proof, regardless of who owns the request now.
   * Does not call provider, and cannot let an old owner overwrite a conflicting outcome. */
  public Outcome adoptProof(String rid) throws SQLException {
    try(Connection c=connect()) {begin(c);try {
      lockRequest(c,rid);
      String key=query(c,"SELECT idem_key FROM p9_requests WHERE rid=?",rid);
      if(key==null){rollback(c);return Outcome.STALE;}
      String decision=decisiveProof(c,rid,key);
      if(decision==null){rollback(c);return Outcome.STALE;}
      String oldStage=query(c,"SELECT stage FROM p9_requests WHERE rid=?",rid);
      if("CONFLICT".equals(decision)) {
        // Terminal business outcome remains immutable. A contrary later proof creates a
        // durable incident, not a rewrite of the already published business outcome.
        if(query(c,"SELECT rid FROM p14_evidence_incident WHERE rid=?",rid)==null)
          exec(c,"INSERT INTO p14_evidence_incident(rid,detected_ms,kind) VALUES (?,?,?)",rid,authoritativeNow(c),"CONTRADICTORY_PROOF");
        if(!Set.of("PAUSED","DONE","REJECTED").contains(oldStage)) exec(c,"UPDATE p9_requests SET stage='PAUSED',ver=ver+1,owner=NULL,dispatch=NULL,lease_ms=0,due_ms=0 WHERE rid=?",rid);
        return commit(c)==Outcome.COMMITTED?Outcome.CONFLICT:Outcome.COMMIT_UNKNOWN;
      }
      if("DONE".equals(oldStage)||"REJECTED".equals(oldStage)) {
        rollback(c);return decision.equals(oldStage)?Outcome.ALREADY:Outcome.CONFLICT;
      }
      String did=null,owner=null,oid=null;long epoch=-1;
      try(PreparedStatement p=c.prepareStatement("SELECT o.oid,d.did,d.owner,d.owner_epoch FROM p9_observation o JOIN p9_dispatch d ON d.did=o.did WHERE d.rid=? AND o.action IN (?,?) ORDER BY o.oid LIMIT 1")) {
        p.setString(1,rid);p.setString(2,"DONE".equals(decision)?"ADOPT_ACTIVATED":"ADOPT_REJECTED");p.setString(3,"DONE".equals(decision)?"CONTRACT_SUCCESS":"CONTRACT_REJECTION");
        try(ResultSet rs=p.executeQuery()) {if(rs.next()){oid=rs.getString(1);did=rs.getString(2);owner=rs.getString(3);epoch=rs.getLong(4);}}
      }
      if(oid==null){rollback(c);return Outcome.STALE;}
      exec(c,"UPDATE p9_requests SET stage=?,ver=ver+1,owner=NULL,dispatch=NULL,lease_ms=0,due_ms=0 WHERE rid=?",decision,rid);
      // Identical evidence has stable settlement ID; the record is tied to original dispatch.
      exec(c,"INSERT INTO p9_settlement(sid,rid,did,owner,lease_ver,target,retry_due_ms) VALUES (?,?,?,?,?,?,0)",oid,rid,did,owner,epoch,decision);
      return commit(c);
    }catch(SQLException ex){rollback(c);throw ex;}}
  }
  public void schema() throws SQLException {
    String[] ddl={
      "CREATE TABLE p16_policy (revision VARCHAR(64) PRIMARY KEY, spec VARCHAR(3000) NOT NULL)",
      "CREATE TABLE p9_requests (rid VARCHAR(40) PRIMARY KEY, idem_key VARCHAR(100) NOT NULL, fingerprint VARCHAR(100) NOT NULL, stage VARCHAR(24) NOT NULL, ver BIGINT NOT NULL, owner VARCHAR(40), dispatch VARCHAR(40), attempts INTEGER NOT NULL, due_ms BIGINT NOT NULL, lease_ms BIGINT NOT NULL, audit_ms BIGINT NOT NULL, policy_revision VARCHAR(64) REFERENCES p16_policy(revision), retention_ms BIGINT NOT NULL DEFAULT 0, customer_id VARCHAR(100), product_code VARCHAR(100), first_auth_ms BIGINT, safe_until_ms BIGINT, CONSTRAINT p9_uk_idem UNIQUE(idem_key))",
      "CREATE TABLE p9_dispatch (did VARCHAR(40) PRIMARY KEY, rid VARCHAR(40) NOT NULL, owner VARCHAR(40) NOT NULL, owner_epoch BIGINT NOT NULL, CONSTRAINT p9_fk_d FOREIGN KEY(rid) REFERENCES p9_requests(rid))",
      "CREATE TABLE p18_pre_http_cancel (did VARCHAR(40) PRIMARY KEY REFERENCES p9_dispatch(did), rid VARCHAR(40) NOT NULL REFERENCES p9_requests(rid), owner VARCHAR(40) NOT NULL, owner_epoch BIGINT NOT NULL, reason VARCHAR(36) NOT NULL, recorded_ms BIGINT NOT NULL, released BOOLEAN NOT NULL, release_stage VARCHAR(24))",
      "CREATE TABLE p9_observation (oid VARCHAR(40) PRIMARY KEY, did VARCHAR(40) NOT NULL, body VARCHAR(200) NOT NULL, http_status INTEGER, policy_revision VARCHAR(64), action VARCHAR(32), evidence VARCHAR(20), evidence_level VARCHAR(20), decision_hash VARCHAR(64), source VARCHAR(30) NOT NULL DEFAULT 'HTTP_RESPONSE', CONSTRAINT p9_fk_o FOREIGN KEY(did) REFERENCES p9_dispatch(did))",
      "CREATE TABLE p9_settlement (sid VARCHAR(40) PRIMARY KEY, rid VARCHAR(40) NOT NULL, did VARCHAR(40) NOT NULL, owner VARCHAR(40) NOT NULL, lease_ver BIGINT NOT NULL, target VARCHAR(20) NOT NULL, retry_due_ms BIGINT NOT NULL, CONSTRAINT p9_fk_s FOREIGN KEY(did) REFERENCES p9_dispatch(did), CONSTRAINT p9_fk_sr FOREIGN KEY(rid) REFERENCES p9_requests(rid))",
      "CREATE TABLE p14_evidence_incident (rid VARCHAR(40) PRIMARY KEY, detected_ms BIGINT NOT NULL, kind VARCHAR(40) NOT NULL, CONSTRAINT p14_fk_inc FOREIGN KEY(rid) REFERENCES p9_requests(rid))",
      "CREATE TABLE p20_signed_attestation (oid VARCHAR(40) PRIMARY KEY REFERENCES p9_observation(oid), did VARCHAR(40) NOT NULL REFERENCES p9_dispatch(did), issuer VARCHAR(80) NOT NULL, kid VARCHAR(80) NOT NULL, nonce VARCHAR(80) NOT NULL, reported_outcome VARCHAR(16) NOT NULL, effect_count INTEGER NOT NULL, generation INTEGER NOT NULL, signed_at_ms BIGINT NOT NULL, token TEXT NOT NULL)",
      "CREATE TABLE p20_rejected_attestation (id VARCHAR(40) PRIMARY KEY, did VARCHAR(40) NOT NULL REFERENCES p9_dispatch(did), rid VARCHAR(40) NOT NULL REFERENCES p9_requests(rid), reason VARCHAR(48) NOT NULL, token_sha256 VARCHAR(64) NOT NULL, at_ms BIGINT NOT NULL)",
      "CREATE TABLE p9_outbox (nid VARCHAR(100) PRIMARY KEY, rid VARCHAR(40) NOT NULL, due_ms BIGINT NOT NULL, token VARCHAR(40), token_until BIGINT NOT NULL, pub_epoch BIGINT DEFAULT 0 NOT NULL, published_ms BIGINT, CONSTRAINT p9_fk_n FOREIGN KEY(rid) REFERENCES p9_requests(rid))"
    };
    try(Connection c=connect();Statement s=c.createStatement()){if(url.startsWith("jdbc:hsqldb:"))s.execute("SET DATABASE TRANSACTION CONTROL MVCC");for(String d:ddl)s.executeUpdate(d);
      // For this HSQLDB file-backed test, require synchronous transaction-log writes.
      if (url.startsWith("jdbc:hsqldb:")) s.execute("SET WRITE_DELAY FALSE");}
  }
  public void registerPolicy(P15Policy p) throws SQLException {
    try(Connection c=connect()) {
      exec(c,"INSERT INTO p16_policy(revision,spec) VALUES (?,?) ON CONFLICT DO NOTHING",p.revision,p.spec);
      String existing=query(c,"SELECT spec FROM p16_policy WHERE revision=?",p.revision);
      if(!p.spec.equals(existing))throw new IllegalStateException("policy revision collision");
    }
  }
  public Outcome admit(String rid,String key,String hash,long now) throws SQLException {
    return admit(rid,key,hash,now,P15Policy.parse(P15Policy.DEFAULT),0);
  }
  public Outcome admit(String rid,String key,String hash,long now,P15Policy policy,long retentionMs) throws SQLException {
    if(retentionMs<0)throw new IllegalArgumentException("negative retention");
    registerPolicy(policy);
    try(Connection c=connect()){
      begin(c);
      try {
        now=authoritativeNow(c);
        exec(c,"INSERT INTO p9_requests (rid,idem_key,fingerprint,stage,ver,attempts,due_ms,lease_ms,audit_ms,policy_revision,retention_ms) VALUES (?,?,?,'READY',0,0,?,0,?,?,?)",rid,key,hash,now,now+1000,policy.revision,retentionMs);
        exec(c,"INSERT INTO p9_outbox (nid,rid,due_ms,token_until) VALUES (?,?,?,0)","initial:"+rid,rid,now);
        return commit(c);
      }catch(SQLException ex){rollback(c);try(Connection check=connect()) {
        String old=query(check,"SELECT fingerprint FROM p9_requests WHERE idem_key=?",key);
        if(old!=null){
          String priorRid=query(check,"SELECT rid FROM p9_requests WHERE idem_key=?",key);
          return rid.equals(priorRid)&&hash.equals(old)&&policy.revision.equals(query(check,"SELECT policy_revision FROM p9_requests WHERE rid=?",rid))?Outcome.ALREADY:Outcome.CONFLICT;
        }
        // Same request identifier with a different key is a conflict, not a transient 503.
        if(query(check,"SELECT idem_key FROM p9_requests WHERE rid=?",rid)!=null)return Outcome.CONFLICT;
      }throw ex;}
    }
  }
  public Outcome acquire(String rid,long expect,String owner,long now,long leaseMs)throws SQLException{
    try(Connection c=connect()) {begin(c);try{
      lockRequest(c,rid);now=authoritativeNow(c);
      String key=query(c,"SELECT idem_key FROM p9_requests WHERE rid=?",rid);
      if(key!=null && decisiveProof(c,rid,key)!=null){rollback(c);return Outcome.STALE;}
      int n=exec(c,"UPDATE p9_requests SET stage='OWNED',ver=ver+1,owner=?,lease_ms=? WHERE rid=? AND ver=? AND stage IN ('READY','WAITING') AND due_ms<=?",owner,now+leaseMs,rid,expect,now);
      if(n!=1){rollback(c);return Outcome.STALE;}return commit(c);
    }catch(SQLException ex){rollback(c);throw ex;}}
  }
  /** Invariant: authorization and budget are committed together with the unique D. */
  public Outcome authorize(String rid,String owner,long expectedOwnerEpoch,String did,long now,int max, String crash)throws SQLException{
    return authorize(rid,owner,expectedOwnerEpoch,did,now,max,crash,2500,1500);
  }
  public Outcome authorize(String rid,String owner,long expectedOwnerEpoch,String did,long now,int max, String crash,long outboundBoundMs,long safetyMs)throws SQLException{
    if(outboundBoundMs<0||safetyMs<0)throw new IllegalArgumentException("time bounds");
    try(Connection c=connect()){begin(c);try{
      lockRequest(c,rid);now=authoritativeNow(c);
      String key=query(c,"SELECT idem_key FROM p9_requests WHERE rid=?",rid);
      if(key!=null && decisiveProof(c,rid,key)!=null){rollback(c);return Outcome.STALE;}
      long attempts=queryLong(c,"SELECT attempts FROM p9_requests WHERE rid=?",rid);
      long retentionMs=queryLong(c,"SELECT retention_ms FROM p9_requests WHERE rid=?",rid);
      long firstMs=queryLong(c,"SELECT first_auth_ms FROM p9_requests WHERE rid=?",rid);
      long safeUntil=queryLong(c,"SELECT safe_until_ms FROM p9_requests WHERE rid=?",rid);
      if(attempts>0 && (retentionMs<=0 || safeUntil<0 || now+outboundBoundMs>=safeUntil)) {
        exec(c,"UPDATE p9_requests SET stage='PAUSED',ver=ver+1,owner=NULL,lease_ms=0,dispatch=NULL,due_ms=0 WHERE rid=? AND stage='OWNED' AND owner=? AND ver=?",rid,owner,expectedOwnerEpoch);
        return commit(c)==Outcome.COMMITTED?Outcome.STALE:Outcome.COMMIT_UNKNOWN;
      }
      long newSafeUntil=(attempts==0 && retentionMs>0)?now+retentionMs-safetyMs:safeUntil;
      int n=exec(c,"UPDATE p9_requests SET stage='IN_FLIGHT',ver=ver+1,dispatch=?,attempts=attempts+1,first_auth_ms=COALESCE(first_auth_ms,?),safe_until_ms=COALESCE(safe_until_ms,?) WHERE rid=? AND stage='OWNED' AND owner=? AND ver=? AND lease_ms>? AND dispatch IS NULL AND attempts<?",did,now,newSafeUntil>0?newSafeUntil:null,rid,owner,expectedOwnerEpoch,now,max);
      if(n!=1){rollback(c);return dispatchMatch(rid,owner,expectedOwnerEpoch,did)?Outcome.ALREADY:Outcome.STALE;}
      exec(c,"INSERT INTO p9_dispatch (did,rid,owner,owner_epoch) VALUES (?,?,?,?)",did,rid,owner,expectedOwnerEpoch);
      if("before".equals(crash))Runtime.getRuntime().halt(71); // uncommitted writes must roll back
      Outcome result=commit(c);
      if("after".equals(crash))Runtime.getRuntime().halt(72);  // durable D, lost caller reply
      return result;
    }catch(SQLException ex){rollback(c);throw ex;}}
  }
  public boolean reconcileDispatch(String did)throws SQLException {try(Connection c=connect()){return query(c,"SELECT did FROM p9_dispatch WHERE did=?",did)!=null;}}
  /** A globally existing D is NOT evidence that it belongs to this request/owner. */
  public boolean dispatchMatch(String rid,String owner,long ownerEpoch,String did)throws SQLException {
    try(Connection c=connect()){
      return query(c,"SELECT did FROM p9_dispatch WHERE did=? AND rid=? AND owner=? AND owner_epoch=?",did,rid,owner,ownerEpoch)!=null;
    }
  }

  /** P18: append-only, local observation: THIS worker path canceled BEFORE invoking HTTP.
   * It is NOT remote evidence and cannot prove that other epochs have not sent HTTP.
   * Serialized against authority transitions using the same request-row lock.
   * Charge is NEVER refunded and any current authority release is atomic with this fact.
   * A stale/expired worker MAY record the local observation, but MUST NOT update state.
   */
  public record CancelResult(Outcome outcome,boolean released,String releaseStage) {}
  public CancelResult cancelBeforeHttp(String rid,String owner,long expectedInFlightVersion,
        String did,String reason,long retryDelayMs,int maxAttempts,String crash) throws SQLException {
    if(!Set.of("LEASE_BUDGET","RETENTION_CUTOFF","AUTHORITY_CHANGED").contains(reason))
      throw new IllegalArgumentException("invalid cancel reason");
    if(retryDelayMs<0||maxAttempts<1)throw new IllegalArgumentException("retry policy");
    try(Connection c=connect()){begin(c);try{
      lockRequest(c,rid);
      String identity=null;
      try(PreparedStatement p=c.prepareStatement("SELECT rid,owner,owner_epoch FROM p9_dispatch WHERE did=?")) {
        p.setString(1,did);try(ResultSet rs=p.executeQuery()) {
          if(rs.next()) {
            if(!rid.equals(rs.getString(1))||!owner.equals(rs.getString(2))||expectedInFlightVersion!=rs.getLong(3)+1){rollback(c);return new CancelResult(Outcome.CONFLICT,false,null);}
            identity=did;
          }
        }
      }
      if(identity==null){rollback(c);return new CancelResult(Outcome.STALE,false,null);}
      try(PreparedStatement p=c.prepareStatement("SELECT rid,owner,owner_epoch,reason,released,release_stage FROM p18_pre_http_cancel WHERE did=?")) {
        p.setString(1,did);try(ResultSet rs=p.executeQuery()) {if(rs.next()) {
          boolean identical=rid.equals(rs.getString(1))&&owner.equals(rs.getString(2))&&
            expectedInFlightVersion==rs.getLong(3)+1&&reason.equals(rs.getString(4));
          boolean released=rs.getBoolean(5);String stage=rs.getString(6);
          rollback(c);return new CancelResult(identical?Outcome.ALREADY:Outcome.CONFLICT,released,stage);
        }}
      }
      long now=authoritativeNow(c);
      String stage=null;
      int n=0;
      if(!"AUTHORITY_CHANGED".equals(reason)){
        long attempts=queryLong(c,"SELECT attempts FROM p9_requests WHERE rid=?",rid);
        stage="RETENTION_CUTOFF".equals(reason)||attempts>=maxAttempts?"PAUSED":"WAITING";
        n=exec(c,"UPDATE p9_requests SET stage=?,ver=ver+1,owner=NULL,dispatch=NULL,lease_ms=0,due_ms=? WHERE rid=? AND owner=? AND dispatch=? AND ver=? AND stage='IN_FLIGHT' AND lease_ms>?",
          stage,"WAITING".equals(stage)?now+retryDelayMs:0L,rid,owner,did,expectedInFlightVersion,now);
        if(n==1&&"WAITING".equals(stage))
          exec(c,"INSERT INTO p9_outbox(nid,rid,due_ms,token_until) VALUES (?,?,?,0)","abort:"+did,rid,now+retryDelayMs);
      }
      if(n==0)stage=null;
      exec(c,"INSERT INTO p18_pre_http_cancel(did,rid,owner,owner_epoch,reason,recorded_ms,released,release_stage) VALUES (?,?,?,?,?,?,?,?)",
          did,rid,owner,expectedInFlightVersion-1,reason,now,n==1,stage);
      if("before".equals(crash)){System.err.flush();Runtime.getRuntime().halt(91);}
      Outcome result=commit(c);
      if("after".equals(crash)){System.err.flush();Runtime.getRuntime().halt(92);}
      return new CancelResult(result,n==1,stage);
    }catch(SQLException ex){rollback(c);throw ex;}}
  }
  public String cancelRecord(String did)throws SQLException{
    try(Connection c=connect();PreparedStatement p=c.prepareStatement(
      "SELECT reason,released,COALESCE(release_stage,'NONE') FROM p18_pre_http_cancel WHERE did=?")) {
      p.setString(1,did);try(ResultSet r=p.executeQuery()){
        return r.next()?r.getString(1)+":"+r.getBoolean(2)+":"+r.getString(3):null;
      }
    }
  }

  /** Atomic durable observation, classifies under the request's pinned policy revision.
   * Only actions stored as ADOPT_* / CONTRACT_* may later be adopted as final proof.
   * Caller may reconcile a COMMIT_UNKNOWN by repeating the SAME oid and decision.
   */
  public Outcome observeHttp(String oid,String did,Integer status,String receipt) throws SQLException {
    return observeHttp(oid,did,status,receipt,"HTTP_RESPONSE");
  }
  public Outcome observeHttp(String oid,String did,Integer status,String receipt,String source) throws SQLException {
    if(!Set.of("HTTP_RESPONSE","PROVIDER_LOOKUP").contains(source))throw new IllegalArgumentException("observation source");
    try(Connection c=connect()){begin(c);try{
      String rid=query(c,"SELECT rid FROM p9_dispatch WHERE did=?",did);
      if(rid==null){rollback(c);return Outcome.STALE;}
      lockRequest(c,rid);
      String revision=query(c,"SELECT policy_revision FROM p9_requests WHERE rid=?",rid);
      String spec=query(c,"SELECT spec FROM p16_policy WHERE revision=?",revision);
      if(spec==null)throw new SQLException("missing pinned response policy");
      P15Policy policy=P15Policy.parse(spec);
      if(!revision.equals(policy.revision))throw new SQLException("policy revision mismatch");
      String key=query(c,"SELECT idem_key FROM p9_requests WHERE rid=?",rid);
      P15Policy.Decision d=policy.decide(status,receipt,key);
      String fingerprint=P15Policy.hash(did+"\u001f"+status+"\u001f"+receipt+"\u001f"+d.action()+"\u001f"+revision+"\u001f"+source);
      String oldHash=query(c,"SELECT decision_hash FROM p9_observation WHERE oid=?",oid);
      if(oldHash!=null){rollback(c);return oldHash.equals(fingerprint)?Outcome.ALREADY:Outcome.CONFLICT;}
      String level=switch(d.action()){
        case ADOPT_ACTIVATED, ADOPT_REJECTED -> "LEDGER_PROVEN";
        case CONTRACT_SUCCESS, CONTRACT_REJECTION -> "STATUS_CONTRACT";
        default -> "UNKNOWN";
      };
      exec(c,"INSERT INTO p9_observation(oid,did,body,http_status,policy_revision,action,evidence,evidence_level,decision_hash,source) VALUES (?,?,?,?,?,?,?,?,?,?)",
          oid,did,receipt==null?"":receipt,status,revision,d.action().name(),d.evidence().name(),level,fingerprint,source);
      if(d.action()==P15Policy.Action.PAUSE_PROTOCOL){
        String stage=query(c,"SELECT stage FROM p9_requests WHERE rid=?",rid);
        if(Set.of("DONE","REJECTED").contains(stage)){
          exec(c,"INSERT INTO p14_evidence_incident(rid,detected_ms,kind) VALUES (?,?,?) ON CONFLICT DO NOTHING",rid,authoritativeNow(c),"LATE_MALFORMED");
        }else {
          exec(c,"UPDATE p9_requests SET stage='PAUSED',ver=ver+1,owner=NULL,dispatch=NULL,lease_ms=0,due_ms=0 WHERE rid=?",rid);
          exec(c,"INSERT INTO p14_evidence_incident(rid,detected_ms,kind) VALUES (?,?,?) ON CONFLICT DO NOTHING",rid,authoritativeNow(c),"MALFORMED_RECEIPT");
        }
      }
      return commit(c);
    }catch(SQLException ex){rollback(c);throw ex;}}
  }
  /** P20 opt-in authenticated provider report. Verification occurs under the SAME
   * request-row lock and transaction that persists the classified observation and
   * original signed bytes. No unsigned lookup enters this path. */
  public Outcome observeSignedLookup(String oid,String did,int status,String token,String challenge,
                                     P20SignedProof verifier) throws Exception {
    try(Connection c=connect()){begin(c);try{
      String rid=query(c,"SELECT rid FROM p9_dispatch WHERE did=?",did);
      if(rid==null){rollback(c);return Outcome.STALE;}
      lockRequest(c,rid);
      String key=query(c,"SELECT idem_key FROM p9_requests WHERE rid=?",rid);
      P20SignedProof.Verified v=verifier.verify(token,key,challenge,status);
      String revision=query(c,"SELECT policy_revision FROM p9_requests WHERE rid=?",rid);
      String spec=query(c,"SELECT spec FROM p16_policy WHERE revision=?",revision);
      if(spec==null)throw new SQLException("missing pinned policy");
      P15Policy policy=P15Policy.parse(spec);
      if(!policy.revision.equals(revision))throw new SQLException("policy mismatch");
      String receipt=v.mockReceipt(key);
      P15Policy.Decision d=policy.decide(status,receipt,key);
      if((v.outcome().equals("ABSENT")||v.outcome().equals("MULTIPLE")) &&
          Set.of(P15Policy.Action.ADOPT_ACTIVATED,P15Policy.Action.ADOPT_REJECTED,
                 P15Policy.Action.CONTRACT_SUCCESS,P15Policy.Action.CONTRACT_REJECTION).contains(d.action()))
        throw new SecurityException("nonpositive snapshot cannot establish terminal outcome");
      String source="SIGNED_PROVIDER_LOOKUP";
      String fingerprint=P15Policy.hash(did+"\u001f"+status+"\u001f"+receipt+"\u001f"+d.action()+"\u001f"+revision+"\u001f"+source+"\u001f"+token);
      String oldHash=query(c,"SELECT decision_hash FROM p9_observation WHERE oid=?",oid);
      if(oldHash!=null){
        String existingToken=query(c,"SELECT token FROM p20_signed_attestation WHERE oid=?",oid);
        rollback(c);
        return oldHash.equals(fingerprint)&&token.equals(existingToken)?Outcome.ALREADY:Outcome.CONFLICT;
      }
      String level=switch(d.action()){
        case ADOPT_ACTIVATED,ADOPT_REJECTED->"SIGNED_LEDGER_CLAIM";
        case CONTRACT_SUCCESS,CONTRACT_REJECTION->"STATUS_CONTRACT";
        default->"UNKNOWN";
      };
      exec(c,"INSERT INTO p9_observation(oid,did,body,http_status,policy_revision,action,evidence,evidence_level,decision_hash,source) VALUES (?,?,?,?,?,?,?,?,?,?)",
        oid,did,receipt==null?"":receipt,status,revision,d.action().name(),d.evidence().name(),level,fingerprint,source);
      exec(c,"INSERT INTO p20_signed_attestation(oid,did,issuer,kid,nonce,reported_outcome,effect_count,generation,signed_at_ms,token) VALUES (?,?,?,?,?,?,?,?,?,?)",
        oid,did,v.issuer(),v.kid(),v.nonce(),v.outcome(),v.count(),v.generation(),v.issuedAtMs(),token);
      return commit(c);
    }catch(Exception ex){rollback(c);throw ex;}}
  }
  /** Only a rejected untrusted report is recorded; no positive decision is inferred.
   * Later valid proofs are not blocked by one invalid network response. */
  public Outcome recordRejectedP20(String did,String reason,String token) throws SQLException {
    if(!Set.of("UNSIGNED","BAD_SIGNATURE","WRONG_BINDING","STALE","MALFORMED","UNAVAILABLE").contains(reason))
      throw new IllegalArgumentException("p20 rejection reason");
    try(Connection c=connect()){begin(c);try{
      String rid=query(c,"SELECT rid FROM p9_dispatch WHERE did=?",did);
      if(rid==null){rollback(c);return Outcome.STALE;}
      lockRequest(c,rid);
      exec(c,"INSERT INTO p20_rejected_attestation (id,did,rid,reason,token_sha256,at_ms) VALUES (?,?,?,?,?,?)",
        UUID.randomUUID().toString(),did,rid,reason,P15Policy.hash(token==null?"":token),authoritativeNow(c));
      return commit(c);
    }catch(SQLException ex){rollback(c);throw ex;}}
  }
  public String observedAction(String oid)throws SQLException {
    try(Connection c=connect()){return query(c,"SELECT action FROM p9_observation WHERE oid=?",oid);}
  }
  public Outcome observe(String oid,String did,String body)throws SQLException {
    try(Connection c=connect()){begin(c);try {
      String rid=query(c,"SELECT rid FROM p9_dispatch WHERE did=?",did);
      if(rid!=null)lockRequest(c,rid);
      exec(c,"INSERT INTO p9_observation(oid,did,body) VALUES (?,?,?)",oid,did,body);
      return commit(c);
    }catch(SQLException ex){rollback(c);try(Connection v=connect()) {
      String rec=query(v,"SELECT did FROM p9_observation WHERE oid=?",oid);
      if(rec!=null){String old=query(v,"SELECT body FROM p9_observation WHERE oid=?",oid);return rec.equals(did)&&old.equals(body)?Outcome.ALREADY:Outcome.CONFLICT;}
    }throw ex;}}
  }
  public Outcome settle(String rid,String owner,String did,long expectedVersion,String sid,String target,long retryDue,long now,String crash)throws SQLException {
    if(!Set.of("WAITING","PAUSED","DONE","REJECTED").contains(target))throw new IllegalArgumentException("target");
    Outcome old=settlementMatch(sid,rid,owner,did,expectedVersion,target,retryDue);if(old!=null)return old;
    try(Connection c=connect()){begin(c);try{
      lockRequest(c,rid);now=authoritativeNow(c);
      int n=exec(c,"UPDATE p9_requests SET stage=?,ver=ver+1,owner=NULL,dispatch=NULL,lease_ms=0,due_ms=? WHERE rid=? AND owner=? AND dispatch=? AND ver=? AND stage='IN_FLIGHT' AND lease_ms>?",target,"WAITING".equals(target)?retryDue:0L,rid,owner,did,expectedVersion,now);
      if(n!=1){rollback(c);Outcome prior=settlementMatch(sid,rid,owner,did,expectedVersion,target,retryDue);return prior!=null?prior:Outcome.STALE;}
      exec(c,"INSERT INTO p9_settlement(sid,rid,did,owner,lease_ver,target,retry_due_ms) VALUES (?,?,?,?,?,?,?)",sid,rid,did,owner,expectedVersion,target,"WAITING".equals(target)?retryDue:0L);
      if("before".equals(crash))Runtime.getRuntime().halt(73); // state+settlement uncommitted
      if("WAITING".equals(target))exec(c,"INSERT INTO p9_outbox (nid,rid,due_ms,token_until) VALUES (?,?,?,0)","retry:"+sid,rid,retryDue);
      Outcome done=commit(c);
      if("after".equals(crash))Runtime.getRuntime().halt(74); // state+outbox committed, ACK missing
      return done;
    }catch(SQLException ex){rollback(c);Outcome prior=settlementMatch(sid,rid,owner,did,expectedVersion,target,retryDue);if(prior!=null)return prior;throw ex;}}
  }
  private Outcome settlementMatch(String sid,String rid,String owner,String did,long leaseVer,String target,long retryDue)throws SQLException {
    try(Connection c=connect(); PreparedStatement p=c.prepareStatement(
        "SELECT rid, did, owner, lease_ver, target, retry_due_ms FROM p9_settlement WHERE sid=?")) {
      p.setString(1,sid);
      try(ResultSet r=p.executeQuery()) {
        if(!r.next()) return null;
        boolean same=rid.equals(r.getString(1)) && did.equals(r.getString(2))
            && owner.equals(r.getString(3)) && leaseVer==r.getLong(4)
            && target.equals(r.getString(5))
            && ("WAITING".equals(target)?retryDue:0L)==r.getLong(6);
        return same?Outcome.ALREADY:Outcome.CONFLICT;
      }
    }
  }
  /** Legacy lookup retained solely for checking a previously confirmed settlement. */
  private Outcome settlementMatchBrief(String sid,String did,String target)throws SQLException {
    try(Connection c=connect()){
      String d=query(c,"SELECT did FROM p9_settlement WHERE sid=?",sid);
      if(d==null)return null;
      String t=query(c,"SELECT target FROM p9_settlement WHERE sid=?",sid);
      return did.equals(d)&&target.equals(t)?Outcome.ALREADY:Outcome.CONFLICT;
    }
  }
  public Outcome recover(String rid,long expectedVersion,long now,int max,long safetyDelay)throws SQLException{
    try(Connection c=connect()){begin(c);try{
      // Expired OWNED has no committed D; expired IN_FLIGHT retains charged D in journal.
      lockRequest(c,rid);now=authoritativeNow(c);
      String key=query(c,"SELECT idem_key FROM p9_requests WHERE rid=?",rid);
      if(key!=null && decisiveProof(c,rid,key)!=null){rollback(c);return Outcome.STALE;}
      int n=exec(c,"UPDATE p9_requests SET stage=CASE WHEN attempts>=? THEN 'PAUSED' ELSE 'WAITING' END,ver=ver+1,owner=NULL,dispatch=NULL,lease_ms=0,due_ms=? WHERE rid=? AND ver=? AND stage IN ('OWNED','IN_FLIGHT') AND lease_ms<=?",max,now+safetyDelay,rid,expectedVersion,now);
      if(n!=1){rollback(c);return Outcome.STALE;}
      if("WAITING".equals(query(c,"SELECT stage FROM p9_requests WHERE rid=?",rid)))
        exec(c,"INSERT INTO p9_outbox (nid,rid,due_ms,token_until) VALUES (?,?,?,0)","recover:"+rid+":"+expectedVersion,rid,now+safetyDelay);
      return commit(c);
    }catch(SQLException ex){rollback(c);throw ex;}}
  }
  /** Repair is a bounded DB watchdog, not the primary RabbitMQ delivery mechanism. */
  public Outcome repair(String rid,long expectedVersion,long now,long everyMs)throws SQLException{
    try(Connection c=connect()){begin(c);try{
      lockRequest(c,rid);now=authoritativeNow(c);
      String key=query(c,"SELECT idem_key FROM p9_requests WHERE rid=?",rid);
      if(key!=null && decisiveProof(c,rid,key)!=null){rollback(c);return Outcome.STALE;}
      int n=exec(c,"UPDATE p9_requests SET audit_ms=? WHERE rid=? AND ver=? AND stage IN ('READY','WAITING') AND due_ms<=? AND audit_ms<=?",now+everyMs,rid,expectedVersion,now,now);
      if(n!=1){rollback(c);return Outcome.STALE;}
      exec(c,"INSERT INTO p9_outbox(nid,rid,due_ms,token_until) VALUES (?,?,?,0)","repair:"+rid+":"+now,rid,now);
      return commit(c);
    }catch(SQLException ex){rollback(c);throw ex;}}
  }
  /** Outbox reservation outcome includes the generation read inside the committing TX.
   * A separate post-commit epoch lookup would allow an ABA publisher to accidentally
   * learn a successor's epoch after its own lease expired; it is not a safe API. */
  public record Reservation(Outcome outcome, long epoch) {}
  public Reservation reserveWithEpoch(String nid,String token,long now,long until)throws SQLException {
    if(until<=now)throw new IllegalArgumentException("reservation deadline must be in the future");
    try(Connection c=connect()){begin(c);try{
      lockOutbox(c,nid);
      long ttl=until-now;now=authoritativeNow(c);until=now+ttl;
      int n=exec(c,"UPDATE p9_outbox SET token=?,token_until=?,pub_epoch=pub_epoch+1 WHERE nid=? AND published_ms IS NULL AND due_ms<=? AND token_until<=?",token,until,nid,now,now);
      if(n!=1){rollback(c);return new Reservation(Outcome.STALE,-1);}
      long epoch=queryLong(c,"SELECT pub_epoch FROM p9_outbox WHERE nid=?",nid);
      Outcome committed=commit(c);
      return new Reservation(committed, committed==Outcome.COMMITTED?epoch:-1);
    }catch(SQLException ex){rollback(c);throw ex;}}
  }
  /** Legacy result-only convenience, not sufficient to publish safely. */
  public Outcome reserve(String nid,String token,long now,long until)throws SQLException {return reserveWithEpoch(nid,token,now,until).outcome();}
  // Retained previous implementation for diff review only -- no duplicate body.
  /** Caller MUST verify correlated confirm and mandatory routing before calling this. */
  public Outcome markPublished(String nid,String token,long epoch,long now)throws SQLException{
    try(Connection c=connect()){begin(c);try{
      lockOutbox(c,nid);now=authoritativeNow(c);
      int n=exec(c,"UPDATE p9_outbox SET published_ms=? WHERE nid=? AND token=? AND pub_epoch=? AND token_until>? AND published_ms IS NULL",now,nid,token,epoch,now);
      if(n!=1){rollback(c);return Outcome.STALE;}return commit(c);
    }catch(SQLException ex){rollback(c);throw ex;}}
  }
  public long publicationEpoch(String nid)throws SQLException {try(Connection c=connect()){return queryLong(c,"SELECT pub_epoch FROM p9_outbox WHERE nid=?",nid);}}
  public String status(String rid)throws SQLException{
    try(Connection c=connect()){
      return "stage="+query(c,"SELECT stage FROM p9_requests WHERE rid=?",rid)+" ver="+queryLong(c,"SELECT ver FROM p9_requests WHERE rid=?",rid)+" attempts="+queryLong(c,"SELECT attempts FROM p9_requests WHERE rid=?",rid)+" owner="+query(c,"SELECT owner FROM p9_requests WHERE rid=?",rid)+" dispatch="+query(c,"SELECT dispatch FROM p9_requests WHERE rid=?",rid);
    }
  }
  public long count(String table)throws SQLException {
    if(!Set.of("p9_requests","p9_dispatch","p9_observation","p9_settlement","p9_outbox","p18_pre_http_cancel").contains(table))throw new IllegalArgumentException();
    try(Connection c=connect()){return queryLong(c,"SELECT COUNT(*) FROM "+table);}
  }
  public long unpublished()throws SQLException{
    try(Connection c=connect()){return queryLong(c,"SELECT COUNT(*) FROM p9_outbox WHERE published_ms IS NULL");}
  }
  /** Command-line calls are deliberately separate JVM invocations to expose independent connection semantics. */
  public static void main(String[] a)throws Exception{
    if(a.length<5)throw new IllegalArgumentException("<driver> <url> <user> <pass> <command> [command args]");
    Class.forName(a[0]);JdbcRabbitLedger s=new JdbcRabbitLedger(a[1],a[2],a[3]);
    String cmd=a[4]; String[] v=Arrays.copyOfRange(a,5,a.length);
    Object answer=switch(cmd){
      case "schema" -> {s.schema();yield "SCHEMA_CREATED";}
      case "admit" -> s.admit(v[0],v[1],v[2],Long.parseLong(v[3]));
      case "admit_v2" -> s.admit(v[0],v[1],v[2],0,P15Policy.parse(P15Policy.DEFAULT),Long.parseLong(v[3]));
      case "observe_http" -> s.observeHttp(v[0],v[1],Integer.parseInt(v[2]),v[3]);
      case "observe_http_source" -> s.observeHttp(v[0],v[1],Integer.parseInt(v[2]),v[3],v[4]);
      case "cancel_record" -> s.cancelRecord(v[0]);
      case "cancel_pre_http" -> s.cancelBeforeHttp(v[0],v[1],Long.parseLong(v[2]),v[3],v[4],Long.parseLong(v[5]),Integer.parseInt(v[6]),v[7]);
      case "acquire" -> s.acquire(v[0],Long.parseLong(v[1]),v[2],Long.parseLong(v[3]),Long.parseLong(v[4]));
      case "authorize" -> s.authorize(v[0],v[1],Long.parseLong(v[2]),v[3],Long.parseLong(v[4]),Integer.parseInt(v[5]),v[6]);
      case "dispatch" -> s.reconcileDispatch(v[0]);
      case "adopt" -> s.adoptProof(v[0]);
      case "observe" -> s.observe(v[0],v[1],v[2]);
      case "settle" -> s.settle(v[0],v[1],v[2],Long.parseLong(v[3]),v[4],v[5],Long.parseLong(v[6]),Long.parseLong(v[7]),v[8]);
      case "settlement" -> v.length==3 ? s.settlementMatchBrief(v[0],v[1],v[2])
          : s.settlementMatch(v[0],v[1],v[2],v[3],Long.parseLong(v[4]),v[5],Long.parseLong(v[6]));
      case "recover" -> s.recover(v[0],Long.parseLong(v[1]),Long.parseLong(v[2]),Integer.parseInt(v[3]),Long.parseLong(v[4]));
      case "repair" -> s.repair(v[0],Long.parseLong(v[1]),Long.parseLong(v[2]),Long.parseLong(v[3]));
      case "reserve" -> {Reservation issued=s.reserveWithEpoch(v[0],v[1],Long.parseLong(v[2]),Long.parseLong(v[3]));yield issued.outcome()==Outcome.COMMITTED?"COMMITTED#"+issued.epoch():issued.outcome().name();}
      case "mark" -> s.markPublished(v[0],v[1],Long.parseLong(v[2]),Long.parseLong(v[3]));
      case "epoch" -> s.publicationEpoch(v[0]);
      case "status" -> s.status(v[0]);
      case "count" -> s.count(v[0]);
      case "unpublished" -> s.unpublished();
      default -> throw new IllegalArgumentException("Bad command "+cmd);
    };
    System.out.println(answer);
  }
}
