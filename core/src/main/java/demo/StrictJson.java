package demo;

import java.util.*;

/** Tiny strict JSON object parser for three string-valued input fields.
 * This deliberately does NOT pretend to be a general JSON implementation.
 * Replace with a maintained JSON library before making the API public.
 */
final class StrictJson {
  private final String s;private int i;
  private StrictJson(String s){this.s=s;}
  static Map<String,String> parse(String src){
    StrictJson j=new StrictJson(src);Map<String,String> obj=j.object();j.ws();
    if(j.i!=j.s.length())throw new IllegalArgumentException("trailing content");
    return obj;
  }
  void ws(){while(i<s.length()&&(s.charAt(i)==' '||s.charAt(i)=='\r'||s.charAt(i)=='\n'||s.charAt(i)=='\t'))i++;}
  boolean eat(char c){ws();if(i<s.length()&&s.charAt(i)==c){i++;return true;}return false;}
  void must(char c){if(!eat(c))throw new IllegalArgumentException("expected JSON delimiter");}
  Map<String,String> object(){
    must('{');Map<String,String> m=new LinkedHashMap<>();
    if(eat('}'))return m;
    do{
      String k=str();must(':');String v=str();
      if(m.putIfAbsent(k,v)!=null)throw new IllegalArgumentException("duplicate key");
      if(eat('}'))return m;
      must(',');
    }while(true);
  }
  String str(){
    ws();must('"');StringBuilder b=new StringBuilder();
    while(i<s.length()){
      char c=s.charAt(i++);
      if(c=='"')return validScalars(b.toString());
      if(c<0x20)throw new IllegalArgumentException("control char");
      if(c!='\\'){b.append(c);continue;}
      if(i>=s.length())break;
      c=s.charAt(i++);
      switch(c){
        case '"','\\','/'->b.append(c);
        case 'n'->b.append('\n');case 'r'->b.append('\r');case 't'->b.append('\t');
        case 'b'->b.append('\b');case 'f'->b.append('\f');
        case 'u'->{
          if(i+4>s.length())throw new IllegalArgumentException("short unicode escape");
          String part=s.substring(i,i+4);i+=4;
          try{b.append((char)Integer.parseInt(part,16));}catch(NumberFormatException e){throw new IllegalArgumentException("bad unicode");}
        }
        default->throw new IllegalArgumentException("invalid escape");
      }
    }
    throw new IllegalArgumentException("unterminated string");
  }
  /** JSON escapes must encode Unicode scalar values. A lone UTF-16 surrogate
   * cannot be faithfully persisted as UTF-8 and must not become an ambiguous ID.
   * Valid high+low pairs represent one supplementary scalar and remain valid.
   */
  private static String validScalars(String value){
    for(int n=0;n<value.length();n++){
      char c=value.charAt(n);
      if(Character.isHighSurrogate(c)){
        if(n+1==value.length() || !Character.isLowSurrogate(value.charAt(n+1)))
          throw new IllegalArgumentException("unpaired high surrogate");
        n++;
      } else if(Character.isLowSurrogate(c)){
        throw new IllegalArgumentException("unpaired low surrogate");
      }
    }
    return value;
  }
  static String quote(String s){
    StringBuilder b=new StringBuilder("\"");
    for(char c:s.toCharArray())switch(c){
      case '"'->b.append("\\\"");case '\\'->b.append("\\\\");case '\n'->b.append("\\n");
      case '\r'->b.append("\\r");case '\t'->b.append("\\t");
      default->{if(c<32)b.append(String.format("\\u%04x",(int)c));else b.append(c);}
    }return b.append('"').toString();
  }
}
