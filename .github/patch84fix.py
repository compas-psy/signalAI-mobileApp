from pathlib import Path

path = Path('lib/ui/screens/capital_screen.dart')
text = path.read_text()
text = text.replace('C.successBorder', 'C.greenBorder')
text = text.replace('C.successFaint', 'C.greenFaint')
text = text.replace('C.success', 'C.green')
path.write_text(text)
