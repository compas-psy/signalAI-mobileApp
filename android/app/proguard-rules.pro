# Flutter-приложение без рефлексии на стороне Java, поэтому правил минимум.
# R8 включён (isMinifyEnabled), чтобы релизный APK/AAB был компактнее.

-keep class io.flutter.app.** { *; }
-keep class io.flutter.plugin.** { *; }
-keep class io.flutter.util.** { *; }
-keep class io.flutter.view.** { *; }
-keep class io.flutter.embedding.** { *; }

# Сообщения об ошибках с читаемыми строками в отчётах Play Console.
-keepattributes SourceFile,LineNumberTable
-renamesourcefileattribute SourceFile
