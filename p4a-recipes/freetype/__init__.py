from pythonforandroid.recipes.freetype import FreetypeRecipe as _BaseFreetypeRecipe


class FreetypeRecipe(_BaseFreetypeRecipe):
    # El servidor de GNU Savannah lleva caido varias horas (502/504).
    # Usamos el mismo archivo pero desde SourceForge, que si responde.
    url = "https://downloads.sourceforge.net/project/freetype/freetype2/{version}/freetype-{version}.tar.gz"


recipe = FreetypeRecipe()
