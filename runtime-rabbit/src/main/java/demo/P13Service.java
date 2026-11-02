package demo;

import com.sun.net.httpserver.*;
import com.rabbitmq.client.*;
import java.io.*;
import java.net.*;
import java.net.http.*;
import java.nio.charset.StandardCharsets;
import java.sql.*;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

/** P13 experimental continuous application. Not production: clock, reconcilers and failure policy limited. */
public final class P13Service {
  final String jdbc=System.getenv().getOrDefault("P13_JDBC","jdbc:postgresql://127.0.0.1:55432/postgres?currentSchema=deroyque_p13");
  final JdbcRabbitLedger ledger=new JdbcRabbitLedger(jdbc,System.getenv().getOrDefault("P13_DB_USER","nobody"),System.getenv().getOrDefault("P13_DB_PASSWORD",""));
  final P15Policy policy=loadPolicy();
  final long retentionMs=Long.parseLong(System.getenv().getOrDefault("P16_PROVIDER_RETENTION_MS","0"));
  static P15Policy loadPolicy(){
    try{
      String path=System.getenv("P16_POLICY_FILE");
      return P15Policy.parse(path==null?P15Policy.DEFAULT:java.nio.file.Files.readString(java.nio.file.Path.of(path)));
    }catch(java.io.IOException e){throw new IllegalStateException("response policy",e);}
  }
  final String queue=System.getenv().getOrDefault("P13_QUEUE","deroyque.p13.jobs");
  final String provider=System.getenv().getOrDefault("P13_PROVIDER_URL","http://127.0.0.1:18082/activate");
  final String reconcileUrl=System.getenv().getOrDefault("P19_RECONCILE_URL","");
  final boolean queryEnabled=!reconcileUrl.isBlank();
  final boolean signedMode="1".equals(System.getenv("P20_SIGNED_LOOKUP_REQUIRED"));
  final P20SignedProof signedVerifier=initP20Verifier();
  P20SignedProof initP20Verifier(){
    if(!signedMode)return null;
    if(!queryEnabled)throw new IllegalStateException("P20 requires reconciliation URL");
    try{return new P20SignedProof(java.nio.file.Path.of(System.getenv().getOrDefault("P20_PUBLIC_KEY_PEM","")),
      System.getenv().getOrDefault("P20_EXPECTED_ISSUER",""),System.getenv().getOrDefault("P20_EXPECTED_KID",""),2500);}
    catch(Exception ex){throw new IllegalStateException("P20 verifier fail-fast",ex);}
  }
  final ConcurrentMap<String,Long> p20PausedPolls=new ConcurrentHashMap<>();
  final String instance=UUID.randomUUID().toString();
  final ProviderHttpGateway providerGateway=new ProviderHttpGateway(provider,reconcileUrl,signedMode);
  final ScheduledExecutorService tasks=Executors.newScheduledThreadPool(2);
  final AtomicInteger processed=new AtomicInteger();
  final RabbitDelivery rabbit=new RabbitDelivery(ledger,queue,this::handle,this::now);
  final AtomicBoolean crashBeforeCancelCommit=new AtomicBoolean("1".equals(System.getenv("P18_CRASH_BEFORE_CANCEL_COMMIT_ONCE")));
  final AtomicBoolean crashAfterCancelCommit=new AtomicBoolean("1".equals(System.getenv("P18_CRASH_AFTER_CANCEL_COMMIT_ONCE")));
  final AtomicBoolean crashAfterAuthorize=new AtomicBoolean("1".equals(System.getenv("P13_CRASH_AFTER_AUTH_ONCE")));
  final AtomicBoolean crashAfterObservation=new AtomicBoolean("1".equals(System.getenv("P14_CRASH_AFTER_OBSERVE_ONCE")));
  final long holdBeforeObservation=Long.parseLong(System.getenv().getOrDefault("P14_HOLD_BEFORE_OBSERVE_MS","0"));
  final AtomicBoolean crashAfterProvider=new AtomicBoolean("1".equals(System.getenv("P13_CRASH_AFTER_HTTP_ONCE")));
  volatile boolean running=true;
  P13Service(){}
  long now()throws SQLException{
    try(java.sql.Connection c=ledger.connect();Statement st=c.createStatement();ResultSet r=st.executeQuery("SELECT (EXTRACT(EPOCH FROM clock_timestamp())*1000)::BIGINT")){r.next();return r.getLong(1);}
  }
  record Row(String rid,String key,String mode,String stage,long ver,long attempts,long due,long lease){}
  Row load(String rid)throws SQLException{
    try(java.sql.Connection c=ledger.connect();PreparedStatement p=c.prepareStatement("SELECT rid,idem_key,fingerprint,stage,ver,attempts,due_ms,lease_ms FROM p9_requests WHERE rid=?")){
      p.setString(1,rid);try(ResultSet r=p.executeQuery()){return r.next()?new Row(r.getString(1),r.getString(2),r.getString(3),r.getString(4),r.getLong(5),r.getLong(6),r.getLong(7),r.getLong(8)):null;}
    }
  }
  ProviderHttpGateway.Lookup lookup(String key){return providerGateway.lookup(key);}
  String p20RejectionReason(ProviderHttpGateway.Lookup v,Exception ex){
    if(v==null)return "UNAVAILABLE";
    if(v.attestation()==null)return "UNSIGNED";
    String msg=String.valueOf(ex.getMessage());
    if(msg.contains("signature"))return "BAD_SIGNATURE";
    if(msg.contains("stale")||msg.contains("future"))return "STALE";
    if(msg.contains("challenge")||msg.contains("key")||msg.contains("issuer"))return "WRONG_BINDING";
    return "MALFORMED";
  }
  JdbcRabbitLedger.Outcome observeP20(String oid,String did,ProviderHttpGateway.Lookup v) throws SQLException {
    try{
      if(v==null||v.attestation()==null)throw new SecurityException("missing attestation");
      return ledger.observeSignedLookup(oid,did,v.status(),v.attestation(),v.challenge(),signedVerifier);
    }catch(SQLException sqlFailure){
      // A database outage / unknown COMMIT must never be misclassified as a bad signature.
      throw sqlFailure;
    }catch(Exception e){
      String reason=p20RejectionReason(v,e);
      System.err.println("P20_UNTRUSTED_PROOF reason="+reason+" error="+e.getClass().getSimpleName());
      var recorded=ledger.recordRejectedP20(did,reason,v==null?null:v.attestation());
      if(recorded!=JdbcRabbitLedger.Outcome.COMMITTED && recorded!=JdbcRabbitLedger.Outcome.ALREADY && recorded!=JdbcRabbitLedger.Outcome.STALE)
        throw new SQLException("P20 rejection journal not confirmed: "+recorded);
      return null;
    }
  }
  void reconcilePaused(){
    if(!queryEnabled)return;
    try(java.sql.Connection c=ledger.connect();PreparedStatement p=c.prepareStatement(
      "SELECT r.rid,r.idem_key,d.did FROM p9_requests r JOIN LATERAL (SELECT did FROM p9_dispatch WHERE rid=r.rid ORDER BY did DESC LIMIT 1) d ON true WHERE r.stage='PAUSED' AND NOT EXISTS (SELECT 1 FROM p9_observation o JOIN p9_dispatch x ON x.did=o.did WHERE x.rid=r.rid AND o.action IN ('ADOPT_ACTIVATED','ADOPT_REJECTED','CONTRACT_SUCCESS','CONTRACT_REJECTION')) AND NOT EXISTS (SELECT 1 FROM p14_evidence_incident i WHERE i.rid=r.rid) LIMIT 20")){
      try(ResultSet rs=p.executeQuery()){
        List<String[]> items=new ArrayList<>();while(rs.next())items.add(new String[]{rs.getString(1),rs.getString(2),rs.getString(3)});
        for(String[] item:items){
          if(signedMode){
            long n=System.currentTimeMillis();
            Long previous=p20PausedPolls.putIfAbsent(item[0],n);
            if(previous!=null && (n-previous<1600 || !p20PausedPolls.replace(item[0],previous,n)))continue;
          }
          ProviderHttpGateway.Lookup v=lookup(item[1]);
          if(signedMode){
            if(v!=null){
              String oid=UUID.randomUUID().toString();
              var saved=observeP20(oid,item[2],v);
              if(saved==JdbcRabbitLedger.Outcome.COMMITTED){
                var finalResult=ledger.adoptProof(item[0]);
                System.out.println("P20_LATE_SIGNED_LOOKUP rid="+item[0]+" outcome="+finalResult);
              }
            }
          }else if(v!=null && v.status()==200 && v.receipt()!=null){
            String oid=UUID.randomUUID().toString();
            var saved=ledger.observeHttp(oid,item[2],200,v.receipt(),"PROVIDER_LOOKUP");
            if(saved==JdbcRabbitLedger.Outcome.COMMITTED){
              var finalResult=ledger.adoptProof(item[0]);
              System.out.println("P19_LATE_LOOKUP rid="+item[0]+" outcome="+finalResult);
            }
          }else if(v!=null&&v.status()==409){
            System.err.println("P19_LOOKUP_AMBIGUOUS rid="+item[0]);
          }
        }
      }
    }catch(Exception e){System.err.println("P19_RECONCILE_ERROR "+e);}
  }
  void watchdog(){
    reconcilePaused();
    rabbit.restoreConsumers();
    try{
      long ms=now();
      // Reconcile durable decisive proofs FIRST, including from expired owner epochs.
      try(java.sql.Connection c=ledger.connect();PreparedStatement p=c.prepareStatement(
          "SELECT DISTINCT d.rid FROM p9_observation o JOIN p9_dispatch d ON d.did=o.did JOIN p9_requests r ON r.rid=d.rid " +
          "WHERE o.action IN ('ADOPT_ACTIVATED','ADOPT_REJECTED','CONTRACT_SUCCESS','CONTRACT_REJECTION') " +
          "AND NOT EXISTS (SELECT 1 FROM p14_evidence_incident i WHERE i.rid=r.rid) " +
          "AND (r.stage NOT IN ('DONE','REJECTED') " +
          "OR (r.stage='DONE' AND o.action IN ('ADOPT_REJECTED','CONTRACT_REJECTION')) " +
          "OR (r.stage='REJECTED' AND o.action IN ('ADOPT_ACTIVATED','CONTRACT_SUCCESS'))) ORDER BY d.rid LIMIT 40")) {
        try(ResultSet proof=p.executeQuery()){
          List<String> ids=new ArrayList<>();while(proof.next())ids.add(proof.getString(1));
          for(String id:ids){var v=ledger.adoptProof(id);
            if(v==JdbcRabbitLedger.Outcome.COMMITTED)System.out.println("P16_RECONCILED rid="+id);
            else if(v==JdbcRabbitLedger.Outcome.CONFLICT)System.err.println("P16_EVIDENCE_CONFLICT rid="+id);
          }
        }
      }
      try(java.sql.Connection c=ledger.connect();PreparedStatement p=c.prepareStatement("SELECT rid,ver,stage FROM p9_requests WHERE ((stage='OWNED' OR stage='IN_FLIGHT') AND lease_ms<=?) OR (stage IN ('READY','WAITING') AND due_ms<=? AND audit_ms<=?) ORDER BY rid LIMIT 30")){
        p.setLong(1,ms);p.setLong(2,ms);p.setLong(3,ms);
        try(ResultSet r=p.executeQuery()){
          List<Object[]> rows=new ArrayList<>();while(r.next())rows.add(new Object[]{r.getString(1),r.getLong(2),r.getString(3)});
          for(Object[] row:rows){String rid=(String)row[0];long v=(long)row[1];String stage=(String)row[2];
            try{
              if("OWNED".equals(stage)||"IN_FLIGHT".equals(stage)){
                // Conservative nonzero safety interval, not a complete provider-idempotency-retention proof.
                var result=ledger.recover(rid,v,now(),3,3200);
                if(result==JdbcRabbitLedger.Outcome.COMMITTED)System.out.println("P13_RECOVER rid="+rid+" from="+stage);
              }else ledger.repair(rid,v,now(),1500);
            }catch(Exception e){System.err.println("P13_REPAIR_ERROR "+e);}
          }
        }
      }
    }catch(Exception e){System.err.println("P13_WATCHDOG_ERROR "+e);}
  }
  /** P18 structured preflight result. The DB check does not create a real-time send guarantee. */
  enum Preflight { ALLOW, LEASE_BUDGET, RETENTION_CUTOFF, AUTHORITY_CHANGED }
  Preflight outboundPreflight(String rid,String did,long outboundBoundMs)throws SQLException{
    try(java.sql.Connection c=ledger.connect();PreparedStatement p=c.prepareStatement(
      "SELECT stage,dispatch,attempts,lease_ms,safe_until_ms, " +
      "(EXTRACT(EPOCH FROM clock_timestamp())*1000)::BIGINT AS db_now FROM p9_requests WHERE rid=?")){
      p.setString(1,rid);
      try(ResultSet r=p.executeQuery()){
        if(!r.next())return Preflight.AUTHORITY_CHANGED;
        long now=r.getLong("db_now"),attempts=r.getLong("attempts");
        if(!"IN_FLIGHT".equals(r.getString("stage"))||!did.equals(r.getString("dispatch")))return Preflight.AUTHORITY_CHANGED;
        if(now+outboundBoundMs>=r.getLong("lease_ms"))return Preflight.LEASE_BUDGET;
        if(attempts>1 && (r.getObject("safe_until_ms")==null||now+outboundBoundMs>=r.getLong("safe_until_ms")))return Preflight.RETENTION_CUTOFF;
        return Preflight.ALLOW;
      }
    }
  }
  void handle(String rid)throws Exception{
    Row row=load(rid);if(row==null){System.err.println("P13_UNKNOWN_HINT rid="+rid);return;}
    // Opt-in laboratory trace. A marker before adoptProof demonstrates that a real
    // consumer entered the SAME durable request while an external PG row lock is held.
    // No filesystem or test rendezvous belongs in the service's claim algorithm.
    final boolean traceRace="1".equals(System.getenv("P21_TRACE_CLAIM_RACE"));
    if(traceRace && ("READY".equals(row.stage())||"WAITING".equals(row.stage())))
      System.out.println("P21_RACE_ENTER rid="+rid+" instance="+instance+" ver="+row.ver());
    var adopted=ledger.adoptProof(rid);
    if(adopted==JdbcRabbitLedger.Outcome.COMMITTED||adopted==JdbcRabbitLedger.Outcome.ALREADY||adopted==JdbcRabbitLedger.Outcome.CONFLICT)return;
    long ms=now();
    if(!Set.of("READY","WAITING").contains(row.stage()) || row.due()>ms)return;
    String owner=UUID.randomUUID().toString();
    var claim=ledger.acquire(rid,row.ver(),owner,ms,4500);
    if(traceRace)System.out.println("P21_RACE_ACQUIRE rid="+rid+" instance="+instance+" result="+claim);
    if(claim!=JdbcRabbitLedger.Outcome.COMMITTED)return;
    String did=UUID.randomUUID().toString();
    var authorized=ledger.authorize(rid,owner,row.ver()+1,did,now(),3,"none");
    if(authorized!=JdbcRabbitLedger.Outcome.COMMITTED)return;
    if(crashAfterAuthorize.compareAndSet(true,false)){System.err.println("P13_FAULT halt after durable authorization");System.err.flush();Runtime.getRuntime().halt(81);}
    long forcedWait = Long.parseLong(System.getenv().getOrDefault("P17_HOLD_BEFORE_RETRY_HTTP_MS", "0"));
    if (row.attempts()>0 && forcedWait>0) {System.out.println("P17_BEFORE_HTTP_RETRY_HOLD " + forcedWait); Thread.sleep(forcedWait);}
    if (!"0".equals(System.getenv().getOrDefault("P17_PREFLIGHT_ENABLED","1"))) {
      Preflight gate=outboundPreflight(rid,did,2500);
      if (gate!=Preflight.ALLOW) {
        // Test-only deliberate pause to expose stale-authority race during journal insertion.
        long afterRejectHold=Long.parseLong(System.getenv().getOrDefault("P18_HOLD_BEFORE_ABORT_RECORD_MS","0"));
        if(afterRejectHold>0)Thread.sleep(afterRejectHold);
        String crash=crashBeforeCancelCommit.compareAndSet(true,false)?"before":
                     crashAfterCancelCommit.compareAndSet(true,false)?"after":"none";
        var result=ledger.cancelBeforeHttp(rid,owner,row.ver()+2,did,gate.name(),650,3,crash);
        if(result.outcome()!=JdbcRabbitLedger.Outcome.COMMITTED&&result.outcome()!=JdbcRabbitLedger.Outcome.ALREADY) 
          throw new IllegalStateException("P18_ABORT_UNCONFIRMED did="+did+" outcome="+result);
        System.out.println("P18_HTTP_NOT_SENT_BY_THIS_WORKER rid="+rid+" did="+did+" reason="+gate+" released="+result.released()+" state="+result.releaseStage());
        return;
      }
    }
    long postflightHold=Long.parseLong(System.getenv().getOrDefault("P17_HOLD_AFTER_PREFLIGHT_RETRY_HTTP_MS","0"));
    if (row.attempts()>0 && postflightHold>0) {
      System.out.println("P17_AFTER_PREFLIGHT_RETRY_HOLD "+postflightHold);
      Thread.sleep(postflightHold);
    }
    Integer status=null;String receipt=null;String target="WAITING";String source="HTTP_RESPONSE";
    try {
      ProviderHttpGateway.PostResult response=providerGateway.post(row.key(),row.mode());
      if(crashAfterProvider.compareAndSet(true,false)){System.err.println("P13_FAULT halt after provider response before observation");System.err.flush();Runtime.getRuntime().halt(82);}
      status=response.status();receipt=response.receipt();
    }catch(Exception e){System.err.println("P16_TRANSPORT_UNCERTAIN "+e.getClass().getSimpleName());}
    ProviderHttpGateway.Lookup signedLookup=null;
    if(signedMode){
      // No unsigned POST response may be used as decisive proof in this profile.
      // Regardless of HTTP 200/422/503, require independently signed query snapshot.
      signedLookup=lookup(row.key());status=409;receipt=null;source="HTTP_RESPONSE";
    }else if(queryEnabled && (status==null || status>=500) && receipt==null){
      ProviderHttpGateway.Lookup found=lookup(row.key());
      if(found!=null && found.status()==200 && found.receipt()!=null){
        status=200;receipt=found.receipt();source="PROVIDER_LOOKUP";
        System.out.println("P19_POSITIVE_LOOKUP rid="+rid);
      } else if(found!=null && found.status()==409){
        status=409;source="PROVIDER_LOOKUP";
        System.err.println("P19_AMBIGUOUS_MULTIPLE_EFFECTS rid="+rid);
      } else System.out.println("P19_NO_POSITIVE_LOOKUP rid="+rid+" (NOT proof of absence)");
    }
    if(holdBeforeObservation>0)Thread.sleep(holdBeforeObservation);
    String oid=UUID.randomUUID().toString();
    JdbcRabbitLedger.Outcome observation;
    if(signedMode){
      observation=observeP20(oid,did,signedLookup);
      if(observation==null){
        // Record a nondecisive local result. Invalid proofs can be retried later.
        observation=ledger.observeHttp(oid,did,409,null,"HTTP_RESPONSE");
      }
    }else observation=ledger.observeHttp(oid,did,status,receipt,source);
    if(observation!=JdbcRabbitLedger.Outcome.COMMITTED&&observation!=JdbcRabbitLedger.Outcome.ALREADY)throw new IllegalStateException("observation unconfirmed "+observation);
    // The response action is read back from the pinned-policy DB record, NOT
    // reclassified using this JVM's (possibly newer) startup policy.
    String persistedAction=ledger.observedAction(oid);
    if(persistedAction==null)throw new IllegalStateException("missing durable decision "+oid);
    target=switch(P15Policy.Action.valueOf(persistedAction)){
      case ADOPT_ACTIVATED,CONTRACT_SUCCESS->"DONE";
      case ADOPT_REJECTED,CONTRACT_REJECTION->"REJECTED";
      case PAUSE_PROTOCOL,PAUSE_UNVERIFIED->"PAUSED";
      default->"WAITING";
    };
    if(crashAfterObservation.compareAndSet(true,false)){
      System.err.println("P14_FAULT crashed after durable observation before adoption");System.err.flush();Runtime.getRuntime().halt(83);
    }
    var reconciled=ledger.adoptProof(rid);
    if(reconciled==JdbcRabbitLedger.Outcome.COMMITTED||reconciled==JdbcRabbitLedger.Outcome.ALREADY||reconciled==JdbcRabbitLedger.Outcome.CONFLICT){
      System.out.println("P14_PROOF_SETTLED rid="+rid+" outcome="+reconciled);return;
    }
    if("WAITING".equals(target)&&row.attempts()+1>=3)target="PAUSED";
    long settledAt=now();long retryAt=settledAt+350L*(row.attempts()+1);
    var settlement=ledger.settle(rid,owner,did,row.ver()+2,UUID.randomUUID().toString(),target,retryAt,settledAt,"none");
    if(settlement!=JdbcRabbitLedger.Outcome.COMMITTED&&settlement!=JdbcRabbitLedger.Outcome.ALREADY){
      System.err.println("P13_SETTLEMENT_UNCERTAIN rid="+rid+" outcome="+settlement+" observation persisted");
      // A lost owner may still have decisive evidence: P13 lacks full late-proof adoption.
      return;
    }
    processed.incrementAndGet();System.out.println("P16_PROCESSED rid="+rid+" target="+target+" action="+persistedAction);
  }
  static Map<String,String> parse(String s){Map<String,String> m=new HashMap<>();for(String pair:s.split("&")){String[]v=pair.split("=",2);if(v.length==2)m.put(URLDecoder.decode(v[0],StandardCharsets.UTF_8),URLDecoder.decode(v[1],StandardCharsets.UTF_8));}return m;}
  static void reply(HttpExchange ex,int status,String msg)throws IOException{byte[]b=msg.getBytes(StandardCharsets.UTF_8);ex.sendResponseHeaders(status,b.length);try(OutputStream os=ex.getResponseBody()){os.write(b);}}
  void api(HttpExchange ex)throws IOException{
    try{
      String path=ex.getRequestURI().getPath();
      if("1".equals(System.getenv("APP_API_ENABLED")) && (path.equals("/submit") || path.equals("/status"))) {
        reply(ex,404,"LEGACY_ENDPOINT_DISABLED");return;
      }
      if(path.equals("/submit")){
        if(!ex.getRequestMethod().equals("POST")){reply(ex,405,"METHOD_NOT_ALLOWED");return;}
        Map<String,String> form=parse(new String(ex.getRequestBody().readAllBytes(),StandardCharsets.UTF_8));
        String rid=form.getOrDefault("rid",""),key=form.getOrDefault("key",""),mode=form.getOrDefault("mode","");
        if(rid.isEmpty()||rid.length()>40||key.isEmpty()||key.length()>100||!Set.of("ok","before500","after500","reject","unconfigured599","malformed","bare200","drop","crash").contains(mode)){reply(ex,400,"BAD_INPUT");return;}
        var outcome=ledger.admit(rid,key,mode,now(),policy,retentionMs);
        reply(ex,outcome==JdbcRabbitLedger.Outcome.CONFLICT?409:outcome==JdbcRabbitLedger.Outcome.COMMIT_UNKNOWN?503:202,outcome.name());
      }else if(path.equals("/status")){
        String rid=parse(ex.getRequestURI().getRawQuery()==null?"":ex.getRequestURI().getRawQuery()).getOrDefault("rid","");
        Row r=load(rid);if(r==null)reply(ex,404,"NOT_FOUND");else reply(ex,200,"stage="+r.stage()+" attempts="+r.attempts()+" version="+r.ver());
      }else if(path.equals("/health"))reply(ex,200,"UP processed="+processed.get()+" published="+rabbit.publishedCount());
      else reply(ex,404,"NOT_FOUND");
    }catch(Exception e){System.err.println("P13_API_ERROR "+e);reply(ex,503,"ERROR "+e.getClass().getSimpleName());}
  }
  private final java.util.concurrent.atomic.AtomicBoolean engineStarted=new java.util.concurrent.atomic.AtomicBoolean();
  private volatile HttpServer jdkApi;
  /** Runtime lifecycle independent of any HTTP framework. Boots the broker consumers,
   * dispatcher and watchdog; it NEVER creates an API listener.
   */
  public synchronized void startEngine() throws Exception {
    if (!engineStarted.compareAndSet(false,true)) return;
    try {
    Class.forName("org.postgresql.Driver");
    ledger.registerPolicy(policy);
    rabbit.start(instance);
    tasks.scheduleWithFixedDelay(rabbit::dispatcher,0,150,TimeUnit.MILLISECONDS);
    tasks.scheduleWithFixedDelay(this::watchdog,0,400,TimeUnit.MILLISECONDS);
    } catch (Exception | Error failure) {
      engineStarted.set(false);
      close();
      throw failure;
    }
  }
  /** Build a database-backed application boundary shared by both HTTP adapters. */
  public RequestOperations requestOperations() {
    return new PublicRequests(ledger,policy,retentionMs);
  }
  /** Legacy JDK HTTP adapter: remains supported for offline regression tests. */
  public void start() throws Exception {
    startEngine();
    startLegacyHttp();
  }
  private void startLegacyHttp() throws Exception {
    RestApi publicApi = null;
    if("1".equals(System.getenv("APP_API_ENABLED"))) {
      publicApi = new RestApi(requestOperations(),
        System.getenv("APP_READ_TOKEN"),System.getenv("APP_WRITE_TOKEN"),
        "1".equals(System.getenv("APP_LAB_METRICS_ENABLED")));
    }
    int port=Integer.parseInt(System.getenv().getOrDefault("P13_API_PORT","18081"));
    HttpServer api=HttpServer.create(new InetSocketAddress("127.0.0.1",port),64);
    final RestApi handler=publicApi;
    api.createContext("/",ex->{
      String path=ex.getRequestURI().getPath();
      if(path.equals("/api/v1/health/liveness")){
        RestApi.json(ex,200,"{\"status\":\"UP\"}");
      }else if(path.equals("/api/v1/health/readiness")){
        try(java.sql.Connection db=ledger.connect();java.sql.Statement q=db.createStatement();java.sql.ResultSet r=q.executeQuery("SELECT 1")){
          if(r.next() && rabbit.isOpen())RestApi.json(ex,200,"{\"status\":\"UP\"}");
          else RestApi.json(ex,503,"{\"status\":\"DOWN\"}");
        }catch(Exception e){RestApi.json(ex,503,"{\"status\":\"DOWN\"}");}
      }else if(path.startsWith("/api/v1/requests") || path.equals("/api/v1/lab/ingress-metrics")) {
        if(handler==null)RestApi.err(ex,404,"PUBLIC_API_DISABLED");
        else handler.handle(ex);
      }else this.api(ex);
    });
    api.setExecutor(Executors.newVirtualThreadPerTaskExecutor());api.start();jdkApi=api;
    System.out.println("P13_SERVICE_READY port="+port+" instance="+instance);System.out.flush();
  }
  /** Liveness of dependencies, excluding provider business availability. */
  public boolean isReady() {
    if(!running||!rabbit.isOpen())return false;
    try(java.sql.Connection db=ledger.connect();java.sql.Statement st=db.createStatement();
        java.sql.ResultSet rs=st.executeQuery("SELECT 1")) {return rs.next();}
    catch(Exception unavailable){return false;}
  }
  /** Shutdown for Spring's bean destruction; never touches shared PG/RabbitMQ servers. */
  public synchronized void close() {
    running=false;
    if(jdkApi!=null){jdkApi.stop(0);jdkApi=null;}
    tasks.shutdownNow();
    rabbit.close();
  }
  public static void main(String[] args)throws Exception{new P13Service().start();}
}
