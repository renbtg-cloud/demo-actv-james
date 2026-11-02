package demo;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.*;
import java.security.spec.*;
import java.util.*;

/** Signed query snapshot validation. Ed25519 authenticates issuer possession, not truth or completeness. */
public final class P20SignedProof {
  private final PublicKey pub; private final String issuer,kid; private final long skewMs;
  public P20SignedProof(Path publicPem,String issuer,String kid,long skewMs) throws Exception {
    if(issuer.isBlank()||kid.isBlank()||!issuer.matches("[A-Za-z0-9_.-]{1,80}")||!kid.matches("[A-Za-z0-9_.-]{1,80}")||skewMs<=0||skewMs>60000)throw new IllegalArgumentException("p20 verifier configuration");
    this.issuer=issuer;this.kid=kid;this.skewMs=skewMs;
    String pem=Files.readString(publicPem,StandardCharsets.US_ASCII);
    if(!pem.matches("(?s)-----BEGIN PUBLIC KEY-----\\s+[A-Za-z0-9+/=\\s]+-----END PUBLIC KEY-----\\s*")) throw new IllegalArgumentException("public key PEM");
    byte[] encoded=Base64.getMimeDecoder().decode(pem.replaceAll("-----BEGIN PUBLIC KEY-----|-----END PUBLIC KEY-----",""));
    this.pub=KeyFactory.getInstance("Ed25519").generatePublic(new X509EncodedKeySpec(encoded));
    if(!"EdDSA".equalsIgnoreCase(pub.getAlgorithm())&&!"Ed25519".equalsIgnoreCase(pub.getAlgorithm()))throw new IllegalArgumentException("not Ed25519 public key");
  }
  public record Verified(String issuer,String kid,String outcome,int count,int generation,long issuedAtMs,String nonce,String signedToken){
    public String mockReceipt(String key) {return outcome.equals("ACTIVATED")||outcome.equals("REJECTED")?"MOCK:"+outcome+":"+key:null;}
  }
  static byte[] from64(String s){if(!s.matches("[A-Za-z0-9_-]{1,6000}"))throw new IllegalArgumentException("malformed base64url");return Base64.getUrlDecoder().decode(s);}
  public Verified verify(String token,String expectedKey,String expectedNonce,int httpStatus) throws Exception {
    if(token==null||token.length()>7000)throw new IllegalArgumentException("missing/oversized attestation");
    String[] parts=token.split("\\.",-1);if(parts.length!=2)throw new IllegalArgumentException("attestation framing");
    byte[] raw=from64(parts[0]),signature=from64(parts[1]);
    if(raw.length>2000||signature.length!=64)throw new IllegalArgumentException("attestation size");
    Signature verifier=Signature.getInstance("Ed25519");verifier.initVerify(pub);verifier.update(raw);
    if(!verifier.verify(signature))throw new SecurityException("bad signature");
    String payload=new String(raw,StandardCharsets.US_ASCII);
    if(!Arrays.equals(payload.getBytes(StandardCharsets.US_ASCII),raw))throw new SecurityException("non-ascii proof");
    String[] v=payload.split("\\n",-1);
    if(v.length!=9||!v[0].equals("P20V1")||!v[1].equals(issuer)||!v[2].equals(kid))throw new SecurityException("wrong proof issuer/version/key-id");
    String key=new String(from64(v[3]),StandardCharsets.UTF_8);
    if(!key.equals(expectedKey)||!v[8].equals(expectedNonce))throw new SecurityException("key or challenge mismatch");
    if(!v[5].matches("0|[1-9][0-9]{0,6}")||!v[6].matches("0|[1-9][0-9]{0,6}")||!v[7].matches("[1-9][0-9]{10,15}"))throw new SecurityException("invalid count/generation/time");
    int count=Integer.parseInt(v[5]),generation=Integer.parseInt(v[6]);long ts=Long.parseLong(v[7]);
    long now=System.currentTimeMillis();if(ts<now-skewMs||ts>now+skewMs)throw new SecurityException("stale/future signed snapshot");
    switch(v[4]){
      case "ABSENT" -> {if(httpStatus!=404||count!=0||generation!=0)throw new SecurityException("inconsistent absence claim");}
      case "MULTIPLE" -> {if(httpStatus!=409||count<2||generation<2)throw new SecurityException("inconsistent multiple claim");}
      case "ACTIVATED","REJECTED" -> {if(httpStatus!=(v[4].equals("ACTIVATED")?200:422)||count!=1||generation!=1)throw new SecurityException("inconsistent positive claim");}
      default-> throw new SecurityException("unknown claim");
    }
    return new Verified(v[1],v[2],v[4],count,generation,ts,v[8],token);
  }
}
