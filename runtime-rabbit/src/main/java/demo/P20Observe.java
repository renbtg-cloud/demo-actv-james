package demo;
import java.nio.file.Path;
import java.nio.file.Files;
/** Lab-only command for testing independently competing JVMs against identical signed observation. */
public final class P20Observe {
  public static void main(String[] a) throws Exception {
    if(a.length!=9)throw new IllegalArgumentException("usage: jdbc publicPem issuer kid oid did status nonce tokenFile");
    var p=new P20SignedProof(Path.of(a[1]),a[2],a[3],2500);
    var ledger=new JdbcRabbitLedger(a[0],"nobody","");
    var r=ledger.observeSignedLookup(a[4],a[5],Integer.parseInt(a[6]),Files.readString(Path.of(a[8])).strip(),a[7],p);
    System.out.println("P20_OBSERVATION "+r);
  }
}
