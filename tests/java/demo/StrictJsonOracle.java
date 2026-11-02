package demo;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.Map;

/** Small independent process exposing the actual core parser to a Python
 * standards-library oracle. Only synthetic one-line test JSON goes over stdin.
 */
public final class StrictJsonOracle {
  public static void main(String[] unused) throws Exception {
    try(BufferedReader in=new BufferedReader(new InputStreamReader(System.in,StandardCharsets.UTF_8))){
      String line;
      while((line=in.readLine())!=null){
        try{
          Map<String,String> result=StrictJson.parse(line);
          String identity=result.get("externalId");
          if(identity==null || !result.keySet().equals(java.util.Set.of("externalId","customerId","productCode")))
            throw new IllegalArgumentException("not an admission object");
          System.out.println("OK\t"+Base64.getEncoder().encodeToString(identity.getBytes(StandardCharsets.UTF_8)));
        }catch(IllegalArgumentException invalid){System.out.println("ERR");}
      }
    }
  }
}
