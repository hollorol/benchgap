# The web server of the local dev stack (compose.yaml): Apache with PHP, as benchgap.net
# runs it, so web/.htaccess (the routing to serve.php, the cache headers) applies as there
FROM php:8.4-apache
RUN a2enmod rewrite headers deflate \
 && sed -i '/<Directory \/var\/www\/>/,/<\/Directory>/ s/AllowOverride None/AllowOverride All/' /etc/apache2/apache2.conf \
 && printf 'memory_limit = 256M\n' > "$PHP_INI_DIR/conf.d/benchgap.ini"
