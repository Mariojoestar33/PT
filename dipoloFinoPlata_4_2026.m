clc;
clear;
close all;

tStart = tic;

% ==============================================================
% DATASET DIPOLO PLATA - VERSION CORREGIDA
%
% Correcciones aplicadas:
%   - Ventana de frecuencia ampliada a +/-50%
%     para capturar la resonancia real del conductor
%   - Validacion de que el minimo de S11 no cae
%     en los bordes del barrido (margen 5%)
%   - Puntos de frecuencia aumentados a 201
%     para mayor resolucion en la curva
% ==============================================================

c = physconst('LightSpeed');

Z0 = 50;

% ==============================================================
% PARAMETROS PRINCIPALES
% ==============================================================

% Aumentado de 151 a 201 para mayor resolucion de la curva
% y reducir la probabilidad de que el minimo quede entre puntos
F_POINTS = 300;

% Margen de borde: si el minimo de S11 cae en el primer o
% ultimo 5% del barrido, la ventana no fue suficiente
EDGE_MARGIN = round(0.05 * F_POINTS);   % ~10 puntos

% ==============================================================
% RADIOS FIJOS
% ==============================================================

a_vals = [
    0.00025
    0.00050
    0.00100
];

% ==============================================================
% MALLA DE LONGITUDES
% ==============================================================

% --------------------------------------------------------------
% 4-6 GHz  |  Paso: 0.2 mm
% --------------------------------------------------------------
L1 = 0.0200:0.0002:0.0350;

% --------------------------------------------------------------
% 2-4 GHz  |  Paso: 0.25 mm
% --------------------------------------------------------------
L2 = 0.0352:0.00025:0.0600;

% --------------------------------------------------------------
% 1-2 GHz  |  Paso: 1 mm
% --------------------------------------------------------------
L3 = 0.0605:0.0010:0.1200;

% --------------------------------------------------------------
% 600 MHz - 1 GHz  |  Paso: 2 mm
% --------------------------------------------------------------
L4 = 0.1210:0.0020:0.2000;

% --------------------------------------------------------------
% 300 - 600 MHz  |  Paso: 5 mm
% --------------------------------------------------------------
L5 = 0.2020:0.0050:0.5000;

% ==============================================================
% CONCATENACION
% ==============================================================

L_vals = [L1 L2 L3 L4 L5];
L_vals = unique(L_vals);
L_vals = L_vals(:);

nL = numel(L_vals);

% ==============================================================
% TOTAL DE SIMULACIONES
% ==============================================================

total_sim = nL * numel(a_vals);

% ==============================================================
% ARCHIVO CSV
% ==============================================================

filename_csv = 'Zin_dipolos_plata_GHz_optimizado_3.csv';

if exist(filename_csv, 'file')
    delete(filename_csv);
end

fid = fopen(filename_csv, 'w');
fprintf(fid, 'L,a,f,Rin,Xin\n');
fclose(fid);

% ==============================================================
% MATERIAL
% ==============================================================

plata = metal('Silver');

% ==============================================================
% CONTADORES
% ==============================================================

rows_total = 0;
sim_ok     = 0;
sim_err    = 0;
sim_skip   = 0;
sim_edge   = 0;   % nuevo: resonancia cayo en el borde

fprintf('============================================\n');
fprintf('INICIO SIMULACION\n');
fprintf('============================================\n');
fprintf('Longitudes:             %d\n',   nL);
fprintf('Radios:                 %d\n',   numel(a_vals));
fprintf('Simulaciones estimadas: %d\n',   total_sim);
fprintf('Puntos por barrido:     %d\n',   F_POINTS);
fprintf('Margen de borde:        %d pts\n', EDGE_MARGIN);
fprintf('============================================\n\n');

% ==============================================================
% LOOP PRINCIPAL
% ==============================================================

for i = 1:nL

    L = L_vals(i);

    % ==========================================================
    % FRECUENCIA CENTRAL APROXIMADA
    % ==========================================================

    f0 = c / (2 * L);

    % ==========================================================
    % VENTANA DE FRECUENCIA CORREGIDA
    %
    % ANTES: +/-30% -> insuficiente para capturar la resonancia
    %        real del dipolo con conductor (plata desplaza f_res)
    %
    % AHORA: +/-50% -> margen suficiente para cualquier radio
    %        dentro del rango geometrico validado
    % ==========================================================

    f_min = 0.50 * f0;
    f_max = 1.50 * f0;

    % Limites globales del sistema
    f_min = max(f_min, 300e6);
    f_max = min(f_max, 6e9);

    if f_min >= f_max
        sim_skip = sim_skip + 1;
        continue;
    end

    % Vector de frecuencias
    f_vals = linspace(f_min, f_max, F_POINTS).';

    % ==========================================================
    % LOOP DE RADIOS
    % ==========================================================

    for j = 1:numel(a_vals)

        a    = a_vals(j);
        ancho = 2 * a;

        % ======================================================
        % VALIDACION GEOMETRICA
        % ======================================================

        if a >= 0.08 * L
            sim_skip = sim_skip + 1;
            continue;
        end

        if ancho >= 0.2 * L
            sim_skip = sim_skip + 1;
            continue;
        end

        try

            % ==================================================
            % CREACION DE ANTENA
            % ==================================================

            ant           = dipole('Length', L, 'Width', ancho);
            ant.Conductor = plata;

            % ==================================================
            % CALCULO DE IMPEDANCIA
            % ==================================================

            Zin = impedance(ant, f_vals);

            R_vals = real(Zin(:));
            X_vals = imag(Zin(:));

            % ==================================================
            % VALIDACION: minimo de S11 no en el borde
            %
            % Si el minimo cae en el primer o ultimo EDGE_MARGIN
            % puntos del barrido, significa que la ventana de
            % frecuencia no fue suficiente para esta geometria.
            % Se descarta el punto para no contaminar el dataset
            % con curvas truncadas.
            % ==================================================

            gamma_vals = (R_vals + 1j*X_vals - Z0) ...
                      ./ (R_vals + 1j*X_vals + Z0);

            s11_vals = 20 * log10( ...
                max(abs(gamma_vals), 1e-12) ...
            );

            [~, idx_min_s11] = min(s11_vals);

            if idx_min_s11 <= EDGE_MARGIN || ...
               idx_min_s11 >= (F_POINTS - EDGE_MARGIN)

                % Resonancia en el borde -> ventana insuficiente
                sim_edge = sim_edge + 1;
                clear ant Zin R_vals X_vals gamma_vals s11_vals
                continue;

            end

            % ==================================================
            % DATASET
            % ==================================================

            chunk = [ ...
                repmat(L, F_POINTS, 1), ...
                repmat(a, F_POINTS, 1), ...
                f_vals, ...
                R_vals, ...
                X_vals  ...
            ];

            % ==================================================
            % ESCRITURA CSV
            % ==================================================

            writematrix( ...
                chunk, ...
                filename_csv, ...
                'WriteMode', 'append' ...
            );

            rows_total = rows_total + size(chunk, 1);
            sim_ok     = sim_ok + 1;

        catch

            sim_err = sim_err + 1;

        end

        clear ant Zin R_vals X_vals gamma_vals s11_vals

    end

    % ==========================================================
    % PROGRESO
    % ==========================================================

    if mod(i, 20) == 0 || i == nL

        elapsed = toc(tStart) / 60;

        fprintf([ ...
            'Progreso: %4d/%4d (%.1f%%) | ' ...
            'OK: %d | ERR: %d | SKIP: %d | EDGE: %d | ' ...
            'Filas: %d | Tiempo: %.2f min\n'], ...
            i, nL, (i/nL)*100, ...
            sim_ok, sim_err, sim_skip, sim_edge, ...
            rows_total, elapsed);

    end

end

% ==============================================================
% RESUMEN FINAL
% ==============================================================

dur_min = toc(tStart) / 60;

fprintf('\n============================================\n');
fprintf('FIN SIMULACION\n');
fprintf('============================================\n');
fprintf('Archivo CSV:   %s\n',    filename_csv);
fprintf('Sim OK:        %d\n',    sim_ok);
fprintf('Sim Error:     %d\n',    sim_err);
fprintf('Sim Skip:      %d\n',    sim_skip);
fprintf('Sim Edge:      %d\n',    sim_edge);
fprintf('  (resonancia en borde, descartadas)\n');
fprintf('Filas CSV:     %d\n',    rows_total);
fprintf('Tiempo total:  %.2f min\n', dur_min);
fprintf('Tiempo total:  %.2f h\n',   dur_min/60);

if exist(filename_csv, 'file')
    file_info = dir(filename_csv);
    fprintf('Tamano CSV:    %.2f MB\n', file_info.bytes / (1024^2));
end

fprintf('============================================\n');