// TEST ONLY - not an implementation of Spring.
package org.springframework.web.bind.annotation;
@java.lang.annotation.Retention(java.lang.annotation.RetentionPolicy.RUNTIME)
public @interface RequestHeader {String value() default "";boolean required() default true;}
