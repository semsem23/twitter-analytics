{#
    Lundi 00:00 de la semaine de `ts`, quel que soit le paramètre WEEK_START.

    date_trunc('week', ...) dépend de WEEK_START sur Snowflake (0 par défaut =
    lundi, mais modifiable au niveau compte / utilisateur / session) ;
    dayofweekiso() vaut toujours 1 = lundi ... 7 = dimanche.
#}
{% macro week_start_monday(ts) -%}
    dateadd('day', 1 - dayofweekiso({{ ts }}), date_trunc('day', {{ ts }}))
{%- endmacro %}
