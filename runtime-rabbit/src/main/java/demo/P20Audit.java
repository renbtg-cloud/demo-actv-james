package demo;
import java.net.*;
import java.net.http.*;
import java.nio.file.Path;
import java.nio.charset.StandardCharsets;
import java.sql.*;
import java.time.Duration;
import java.util.UUID;

/** Manual, lab-only audit of a terminal request's signed snapshot; does NOT re-send activation. */
public final class P20Audit {
  public static void main(String[] args)throws Exception{
    if(args.length!=6)throw new IllegalArgumentException("usage: jdbc lookupUrl pubKey issuer kid rid");
    var ledger=new JdbcRabbitLedger(args[0],"nobody","");
    var verifier=new P20SignedProof(Path.of(args[2]),args[3],args[4],2500);
    String key=null,did=null;
    try(Connection c=ledger.connect();PreparedStatement q=c.prepareStatement(
      "SELECT r.idem_key,d.did FROM p9_requests r JOIN p9_dispatch d ON d.rid=r.rid WHERE r.rid=? ORDER BY d.did DESC LIMIT 1")){
      q.setString(1,args[5]);try(ResultSet rs=q.executeQuery()){if(rs.next()){key=rs.getString(1);did=rs.getString(2);}}
    }
    if(key==null||did==null)throw new IllegalStateException("missing historical dispatch");
    String nonce=UUID.randomUUID().toString();
    var uri=URI.create(args[1]+"?key="+URLEncoder.encode(key,StandardCharsets.UTF_8)+"&nonce="+nonce);
    var req=HttpRequest.newBuilder(uri).timeout(Duration.ofSeconds(3)).GET().build();
    var res=HttpClient.newHttpClient().send(req,HttpResponse.BodyHandlers.ofString());
    String proof=res.headers().firstValue("X-Provider-Attestation").orElse(null);
    String oid=UUID.randomUUID().toString();
    try {
      var observation=ledger.observeSignedLookup(oid,did,res.statusCode(),proof,nonce,verifier);
      var adopted=ledger.adoptProof(args[5]);
      System.out.println("P20_AUDIT accepted_status="+res.statusCode()+" observation="+observation+" adoption="+adopted+" oid="+oid);
    }catch(Exception ex){
      ledger.recordRejectedP20(did,proof==null?"UNSIGNED":"MALFORMED",proof);
      System.out.println("P20_AUDIT_REJECTED "+ex.getClass().getSimpleName()+" "+ex.getMessage());
      System.exit(3);
    }
  }
}
