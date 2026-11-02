package demo;

import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.UUID;

/** Network transport only. A signed claim is not guaranteed truthful; the ledger validates it. */
final class ProviderHttpGateway {
  record PostResult(int status, String receipt) {}
  record Lookup(int status, String receipt, String attestation, String challenge) {}

  private final HttpClient client;
  private final URI activate;
  private final String lookupBase;
  private final boolean signedLookup;

  ProviderHttpGateway(String activateUrl, String lookupUrl, boolean signedLookup) {
    this.client=HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2)).build();
    this.activate=URI.create(activateUrl);
    this.lookupBase=lookupUrl;
    this.signedLookup=signedLookup;
  }

  PostResult post(String key,String mode) throws Exception {
    String form="key="+URLEncoder.encode(key,StandardCharsets.UTF_8)+"&mode="+
       URLEncoder.encode(mode.startsWith("public:")?"ok":mode,StandardCharsets.UTF_8);
    HttpRequest request=HttpRequest.newBuilder(activate).timeout(Duration.ofSeconds(2))
      .header("Content-Type","application/x-www-form-urlencoded")
      .POST(HttpRequest.BodyPublishers.ofString(form)).build();
    HttpResponse<String> response=client.send(request,HttpResponse.BodyHandlers.ofString());
    return new PostResult(response.statusCode(),response.headers().firstValue("X-Provider-Receipt").orElse(null));
  }

  /** Failure, absence, or timeouts never prove that no effect occurred. */
  Lookup lookup(String key) {
    if(lookupBase.isBlank())return null;
    try {
      String nonce=signedLookup?UUID.randomUUID().toString():"";
      String address=lookupBase+"?key="+URLEncoder.encode(key,StandardCharsets.UTF_8)+
        (signedLookup?"&nonce="+URLEncoder.encode(nonce,StandardCharsets.UTF_8):"");
      HttpResponse<String> response=client.send(HttpRequest.newBuilder(URI.create(address))
        .timeout(Duration.ofMillis(1200)).GET().build(),HttpResponse.BodyHandlers.ofString());
      return new Lookup(response.statusCode(),response.headers().firstValue("X-Provider-Receipt").orElse(null),
          response.headers().firstValue("X-Provider-Attestation").orElse(null),nonce);
    }catch(Exception unavailable){
      System.err.println("P19_LOOKUP_UNAVAILABLE "+unavailable.getClass().getSimpleName());
      return null;
    }
  }
}
