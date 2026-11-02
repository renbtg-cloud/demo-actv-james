package org.slf4j;
import java.lang.reflect.*;
/** Isolated P12 fallback when runtime cannot fetch org.slf4j:slf4j-api. Logs are suppressed. */
public final class LoggerFactory {
 private static final Logger NOP = (Logger) Proxy.newProxyInstance(Logger.class.getClassLoader(),new Class<?>[]{Logger.class},(p,m,a)-> {
   if(m.getName().equals("getName")) return "P12-noop";
   if(m.getReturnType()==boolean.class) return false;
   if(m.getReturnType()==int.class) return 0;
   return null;
 });
 public static Logger getLogger(Class<?> cls){return NOP;}
 public static Logger getLogger(String name){return NOP;}
}
