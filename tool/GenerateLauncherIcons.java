import javax.imageio.ImageIO;
import java.awt.Color;
import java.awt.Graphics2D;
import java.awt.RenderingHints;
import java.awt.image.BufferedImage;
import java.io.File;

/**
 * Генератор иконок запуска из логотипа {@code assets/images/logo.jpg}.
 *
 * <p>Почему отдельная утилита, а не пакет вроде flutter_launcher_icons: в
 * проекте намеренно нет ни одной внешней зависимости pub, и ради иконок её
 * заводить не хочется. javax.imageio есть в любом JDK, а JDK и так нужен для
 * сборки Android.
 *
 * <p>Что делает. В логотипе «SignalAI» на иконке читаема только монограмма
 * «ΛI»: словесный знак на 48dp превращается в неразборчивую полоску. Поэтому
 * из логотипа вырезается именно монограмма — это его собственные пиксели, а не
 * придуманный заново знак, — и раскладывается по двум слоям адаптивной иконки:
 * фон — жёлтый логотипа, передний план — глиф с прозрачностью.
 *
 * <p>Прозрачность считается расклейкой по яркости: пиксель либо смесь жёлтого с
 * чёрным, либо смесь жёлтого с белым. Так края остаются сглаженными, без
 * жёлтой каймы вокруг глифа.
 *
 * <p>Запуск: {@code java tool/GenerateLauncherIcons.java} из корня проекта.
 */
public class GenerateLauncherIcons {

  /** Фон логотипа. Он же — фоновый слой адаптивной иконки. */
  static final Color BACKGROUND = new Color(0xFC, 0xCF, 0x04);

  /** Монограмма «ΛI» в координатах logo.jpg (640×640). */
  static final int CROP_X = 418, CROP_Y = 267, CROP_W = 119, CROP_H = 91;

  /** Плотности: имя каталога → сторона легаси-иконки в пикселях. */
  static final Object[][] DENSITIES = {
      {"mdpi", 48}, {"hdpi", 72}, {"xhdpi", 96}, {"xxhdpi", 144}, {"xxxhdpi", 192},
  };

  /**
   * Доля ширины холста под глиф.
   *
   * <p>У адаптивной иконки маска съедает края: гарантированно видна только
   * центральная зона, поэтому на 108dp-холсте глиф занимает 56%, а не всё поле.
   */
  static final double ADAPTIVE_SCALE = 0.56;

  /** У легаси-иконки маски нет, глиф может занимать больше. */
  static final double LEGACY_SCALE = 0.72;

  public static void main(String[] args) throws Exception {
    File logo = new File("assets/images/logo.jpg");
    File resDir = new File("android/app/src/main/res");
    BufferedImage source = ImageIO.read(logo);
    BufferedImage glyph = extractGlyph(source);

    for (Object[] density : DENSITIES) {
      String name = (String) density[0];
      int legacySize = (Integer) density[1];
      File dir = new File(resDir, "mipmap-" + name);
      dir.mkdirs();

      write(legacyIcon(glyph, legacySize), new File(dir, "ic_launcher.png"));
      // Холст переднего плана адаптивной иконки — 108dp против 48dp легаси.
      int foregroundSize = legacySize * 108 / 48;
      write(foreground(glyph, foregroundSize), new File(dir, "ic_launcher_foreground.png"));
    }
    System.out.println("Иконки перегенерированы из " + logo);
  }

  /** Монограмма с альфой: жёлтый фон логотипа становится прозрачным. */
  static BufferedImage extractGlyph(BufferedImage source) {
    BufferedImage out = new BufferedImage(CROP_W, CROP_H, BufferedImage.TYPE_INT_ARGB);
    double backgroundLuma = luma(BACKGROUND.getRed(), BACKGROUND.getGreen(), BACKGROUND.getBlue());

    for (int y = 0; y < CROP_H; y++) {
      for (int x = 0; x < CROP_W; x++) {
        int p = source.getRGB(CROP_X + x, CROP_Y + y);
        int r = (p >> 16) & 255, g = (p >> 8) & 255, b = p & 255;
        double l = luma(r, g, b);

        // Темнее фона — смесь с чёрным, светлее — с белым. Доля примеси и есть
        // альфа: на чистом фоне она нулевая, в теле глифа единичная.
        int colour;
        double alpha;
        if (l <= backgroundLuma) {
          colour = 0x000000;
          alpha = (backgroundLuma - l) / backgroundLuma;
        } else {
          colour = 0xFFFFFF;
          alpha = (l - backgroundLuma) / (255 - backgroundLuma);
        }
        // JPEG шумит: почти прозрачное считаем фоном, почти непрозрачное —
        // телом глифа, иначе по всей иконке остаётся серая рябь.
        if (alpha < 0.06) alpha = 0;
        if (alpha > 0.94) alpha = 1;
        out.setRGB(x, y, ((int) Math.round(alpha * 255) << 24) | colour);
      }
    }
    return out;
  }

  static double luma(int r, int g, int b) {
    return 0.299 * r + 0.587 * g + 0.114 * b;
  }

  /** Квадратная иконка для launcher'ов до Android 8: глиф на жёлтом. */
  static BufferedImage legacyIcon(BufferedImage glyph, int size) {
    BufferedImage out = new BufferedImage(size, size, BufferedImage.TYPE_INT_ARGB);
    Graphics2D g = graphics(out);
    g.setColor(BACKGROUND);
    g.fillRect(0, 0, size, size);
    drawCentred(g, glyph, size, LEGACY_SCALE);
    g.dispose();
    return out;
  }

  /** Передний слой адаптивной иконки: только глиф, фон прозрачный. */
  static BufferedImage foreground(BufferedImage glyph, int size) {
    BufferedImage out = new BufferedImage(size, size, BufferedImage.TYPE_INT_ARGB);
    Graphics2D g = graphics(out);
    drawCentred(g, glyph, size, ADAPTIVE_SCALE);
    g.dispose();
    return out;
  }

  static void drawCentred(Graphics2D g, BufferedImage glyph, int canvas, double scale) {
    int w = (int) Math.round(canvas * scale);
    int h = (int) Math.round(w * (double) glyph.getHeight() / glyph.getWidth());
    g.drawImage(glyph, (canvas - w) / 2, (canvas - h) / 2, w, h, null);
  }

  static Graphics2D graphics(BufferedImage image) {
    Graphics2D g = image.createGraphics();
    g.setRenderingHint(RenderingHints.KEY_INTERPOLATION, RenderingHints.VALUE_INTERPOLATION_BICUBIC);
    g.setRenderingHint(RenderingHints.KEY_RENDERING, RenderingHints.VALUE_RENDER_QUALITY);
    g.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
    return g;
  }

  static void write(BufferedImage image, File file) throws Exception {
    ImageIO.write(image, "png", file);
  }
}
