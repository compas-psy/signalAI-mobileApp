import javax.imageio.ImageIO;
import java.awt.BasicStroke;
import java.awt.Color;
import java.awt.Graphics2D;
import java.awt.RenderingHints;
import java.awt.geom.AffineTransform;
import java.awt.geom.Path2D;
import java.awt.geom.Rectangle2D;
import java.awt.geom.RoundRectangle2D;
import java.awt.image.BufferedImage;
import java.io.File;

/**
 * Генератор иконок запуска: жёлтая молния на тёмной плашке.
 *
 * <p>Почему отдельная утилита, а не пакет вроде flutter_launcher_icons: в
 * проекте намеренно нет ни одной внешней зависимости pub, и ради иконок её
 * заводить не хочется. javax.imageio есть в любом JDK, а JDK и так нужен для
 * сборки Android.
 *
 * <p>Что рисуется. Знак — только молния, без букв: словесный знак «SignalAI» на
 * 48dp превращается в неразборчивую полоску, а монограмма «ΛI», которую
 * вырезали из логотипа раньше, читалась как случайные засечки. Молния —
 * единственная форма, которая держится на всех плотностях.
 *
 * <p>Геометрия совпадает с макетом (`design/SignalAI App v2.dc.html`, §3 ТЗ):
 * путь задан в системе 48×48, фон #17171C, молния #FFD400. Тот же путь
 * нарисован в приложении (`Icons.brandBolt`) — знак в шапке и знак на рабочем
 * столе обязаны быть одним знаком.
 *
 * <p>Запуск: {@code java tool/GenerateLauncherIcons.java} из корня проекта.
 */
public class GenerateLauncherIcons {

  /** Фон плашки — тёмная поверхность, а не жёлтый: молния должна светиться. */
  static final Color BACKGROUND = new Color(0x17, 0x17, 0x1C);

  static final Color BOLT = new Color(0xFF, 0xD4, 0x00);

  /** Тонкая рамка плашки легаси-иконки: отделяет её от тёмных обоев. */
  static final Color EDGE = new Color(0x2C, 0x2C, 0x34);

  /** Плотности: имя каталога → сторона легаси-иконки в пикселях. */
  static final Object[][] DENSITIES = {
      {"mdpi", 48}, {"hdpi", 72}, {"xhdpi", 96}, {"xxhdpi", 144}, {"xxxhdpi", 192},
  };

  /**
   * Доля стороны холста под высоту молнии на адаптивной иконке.
   *
   * <p>У адаптивной иконки маска съедает края: на 108dp-холсте гарантированно
   * видна центральная зона 66dp. 0,55 от 108 — это 59dp, с запасом внутри неё.
   */
  static final double ADAPTIVE_SCALE = 0.55;

  /** У легаси-иконки маски нет — молния занимает больше плашки. */
  static final double LEGACY_SCALE = 0.62;

  public static void main(String[] args) throws Exception {
    File resDir = new File("android/app/src/main/res");

    for (Object[] density : DENSITIES) {
      String name = (String) density[0];
      int legacySize = (Integer) density[1];
      File dir = new File(resDir, "mipmap-" + name);
      dir.mkdirs();

      write(legacyIcon(legacySize), new File(dir, "ic_launcher.png"));
      // Холст переднего плана адаптивной иконки — 108dp против 48dp легаси.
      int foregroundSize = legacySize * 108 / 48;
      write(foreground(foregroundSize), new File(dir, "ic_launcher_foreground.png"));
    }
    System.out.println("Иконки перерисованы: молния " + BOLT + " на " + BACKGROUND);
  }

  /** Молния макета в координатах 48×48. */
  static Path2D bolt() {
    Path2D.Double p = new Path2D.Double();
    p.moveTo(26.6, 11.5);
    p.lineTo(16.5, 27);
    p.lineTo(23.4, 27);
    p.lineTo(21.4, 36.5);
    p.lineTo(31.5, 21.6);
    p.lineTo(24.6, 21.6);
    p.closePath();
    return p;
  }

  /** Квадратная иконка для launcher'ов до Android 8: молния на плашке. */
  static BufferedImage legacyIcon(int size) {
    BufferedImage out = new BufferedImage(size, size, BufferedImage.TYPE_INT_ARGB);
    Graphics2D g = graphics(out);
    double inset = size * 0.02;
    RoundRectangle2D plate = new RoundRectangle2D.Double(
        inset, inset, size - 2 * inset, size - 2 * inset, size * 0.27, size * 0.27);
    g.setColor(BACKGROUND);
    g.fill(plate);
    g.setColor(EDGE);
    g.setStroke(new BasicStroke((float) Math.max(1, size / 48.0)));
    g.draw(plate);
    drawBolt(g, size, LEGACY_SCALE);
    g.dispose();
    return out;
  }

  /** Передний слой адаптивной иконки: только молния, фон прозрачный. */
  static BufferedImage foreground(int size) {
    BufferedImage out = new BufferedImage(size, size, BufferedImage.TYPE_INT_ARGB);
    Graphics2D g = graphics(out);
    drawBolt(g, size, ADAPTIVE_SCALE);
    g.dispose();
    return out;
  }

  /** Молния по центру холста: высота — [scale] от стороны. */
  static void drawBolt(Graphics2D g, int canvas, double scale) {
    Path2D path = bolt();
    Rectangle2D bounds = path.getBounds2D();
    double k = canvas * scale / bounds.getHeight();

    AffineTransform t = new AffineTransform();
    t.translate(
        (canvas - bounds.getWidth() * k) / 2 - bounds.getX() * k,
        (canvas - bounds.getHeight() * k) / 2 - bounds.getY() * k);
    t.scale(k, k);

    g.setColor(BOLT);
    g.fill(t.createTransformedShape(path));
  }

  static Graphics2D graphics(BufferedImage image) {
    Graphics2D g = image.createGraphics();
    g.setRenderingHint(RenderingHints.KEY_INTERPOLATION, RenderingHints.VALUE_INTERPOLATION_BICUBIC);
    g.setRenderingHint(RenderingHints.KEY_RENDERING, RenderingHints.VALUE_RENDER_QUALITY);
    g.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
    g.setRenderingHint(RenderingHints.KEY_STROKE_CONTROL, RenderingHints.VALUE_STROKE_PURE);
    return g;
  }

  static void write(BufferedImage image, File file) throws Exception {
    ImageIO.write(image, "png", file);
  }
}
