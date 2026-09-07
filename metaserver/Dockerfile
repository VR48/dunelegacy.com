FROM php:8.2-apache

# Enable Apache mod_rewrite
RUN a2enmod rewrite && docker-php-ext-install pdo_sqlite

# Copy metaserver files
COPY . /var/www/html/

# Create data directory with proper permissions
RUN mkdir -p /var/www/data && \
    chown -R www-data:www-data /var/www/data && \
    chown -R www-data:www-data /var/www/html

# Set working directory
WORKDIR /var/www/html

# Expose port 8080
EXPOSE 8080

# Configure Apache to listen on port 8080
RUN sed -i 's/80/8080/g' /etc/apache2/sites-available/000-default.conf /etc/apache2/ports.conf

# Start Apache
CMD ["apache2-foreground"]
