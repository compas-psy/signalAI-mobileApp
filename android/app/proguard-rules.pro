# Flutter-приложение без рефлексии на стороне Java, поэтому правил минимум.
# R8 включён (isMinifyEnabled), чтобы релизный APK/AAB был компактнее.

-keep class io.flutter.app.** { *; }
-keep class io.flutter.plugin.** { *; }
-keep class io.flutter.util.** { *; }
-keep class io.flutter.view.** { *; }
-keep class io.flutter.embedding.** { *; }

# Правило выше сохраняет весь io.flutter.embedding, включая
# FlutterPlayStoreSplitApplication и PlayStoreDeferredComponentManager. Они
# ссылаются на Play Core (com.google.android.play.core.**), которого в
# зависимостях нет и быть не должно: deferred components приложение не
# использует. Без этой строки R8 не может разрешить ссылки и валит сборку
# («Missing class com.google.android.play.core.splitcompat.SplitCompatApplication»
# и ещё десяток таких же), то есть релизный APK не собирается вообще.
# Код за этими ссылками недостижим, поэтому подавить предупреждение безопасно.
-dontwarn com.google.android.play.core.**

# Сообщения об ошибках с читаемыми строками в отчётах Play Console.
-keepattributes SourceFile,LineNumberTable
-renamesourcefileattribute SourceFile
