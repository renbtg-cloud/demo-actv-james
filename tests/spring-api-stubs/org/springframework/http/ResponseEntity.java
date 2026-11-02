// TEST ONLY - not an implementation of Spring.
package org.springframework.http;
import java.util.*;
/** Compile-only simplified signature; absolutely NOT a Spring runtime. */
public class ResponseEntity<T> {
  private final int code;private final T body;private final Map<String,String> headers;
  private ResponseEntity(int code,T body,Map<String,String> headers){this.code=code;this.body=body;this.headers=headers;}
  public int getStatusCodeValue(){return code;} public T getBody(){return body;}
  public String getHeader(String key){return headers.get(key);}
  public static Builder status(int code){return new Builder(code);}
  public static Builder ok(){return status(200);}
  public static final class Builder {
    private final int code;private final Map<String,String> headers=new HashMap<>();
    Builder(int code){this.code=code;}
    public Builder contentType(MediaType type){headers.put("Content-Type","application/json");return this;}
    public Builder header(String key,String value){headers.put(key,value);return this;}
    public <T> ResponseEntity<T> body(T v){return new ResponseEntity<>(code,v,new HashMap<>(headers));}
  }
}
